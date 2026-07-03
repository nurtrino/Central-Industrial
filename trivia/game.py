"""
WedgeQuest game engine — the classic wheel-of-wedges trivia game, rules only.

Pure state machine: no IO, no network, no clocks. The server (server.py) feeds it
dice values and questions and schedules phase auto-advances; this module decides
what is legal and what happens next. That split keeps the whole rulebook unit-
testable without a browser or the trivia API.

Board topology (67 spaces):
    • 1 center hub
    • 6 spokes of 5 question spaces each (s{k}-{i}, i=1 innermost)
    • 6 category headquarters on the outer ring (hq{k}) — win a wedge here
    • 6 ring arcs of 5 spaces between adjacent HQs (r{k}-{i}) — the middle
      space of every arc is ROLL AGAIN, the rest are question spaces
Movement is exact die count along the graph, any direction, but a single move
may never revisit a space (no doubling back mid-roll).

Classic rules implemented:
    • correct answer → roll again (same player keeps going)
    • correct answer on an HQ you don't hold → earn that wedge
    • hub before 6 wedges → wild card, mover picks any category
    • hub with all 6 wedges (exact roll only, which exact-movement gives us
      for free) → opponents vote the final category; correct answer wins
"""
from __future__ import annotations

import math

CATEGORIES = [
    {"key": "geo", "name": "Geography",         "color": "#3D6DEB"},
    {"key": "ent", "name": "Entertainment",     "color": "#E85B8A"},
    {"key": "his", "name": "History",           "color": "#F2C230"},
    {"key": "art", "name": "Arts & Literature", "color": "#9B59D0"},
    {"key": "sci", "name": "Science & Nature",  "color": "#3FA45B"},
    {"key": "spo", "name": "Sports & Leisure",  "color": "#E87E2D"},
]
NUM_CATS = len(CATEGORIES)

PLAYER_COLORS = ["#FF5252", "#40C4FF", "#69F0AE", "#FFD740", "#FF6EC7", "#B388FF"]

MAX_PLAYERS = 6
MIN_PLAYERS = 2

# Ring arc pattern between hq{k} and hq{k+1}, walking away from hq{k}:
# category offsets from k, None = ROLL AGAIN. Chosen so every category appears
# exactly 4 times on the ring and no two adjacent spaces share a color.
RING_PATTERN = [2, 5, None, 1, 4]

# Geometry (SVG user units, board centred on 0,0) — sent to clients so the
# server stays the single source of truth for layout.
SPOKE_RADII = [55, 93, 131, 169, 207]
RING_RADIUS = 252


def build_board():
    """Return (nodes, adj): node metadata incl. x/y, and adjacency lists."""
    nodes: dict[str, dict] = {}
    adj: dict[str, list[str]] = {}

    def add(nid, kind, cat, x, y):
        nodes[nid] = {"id": nid, "kind": kind, "cat": cat,
                      "x": round(x, 1), "y": round(y, 1)}
        adj[nid] = []

    def link(a, b):
        adj[a].append(b)
        adj[b].append(a)

    add("hub", "hub", None, 0, 0)
    for k in range(NUM_CATS):
        ang = math.radians(k * 60 - 90)
        dx, dy = math.cos(ang), math.sin(ang)
        for i in range(1, 6):
            r = SPOKE_RADII[i - 1]
            add(f"s{k}-{i}", "cat", (k + i) % NUM_CATS, r * dx, r * dy)
        add(f"hq{k}", "hq", k, RING_RADIUS * dx, RING_RADIUS * dy)
        link("hub", f"s{k}-1")
        for i in range(1, 5):
            link(f"s{k}-{i}", f"s{k}-{i+1}")
        link(f"s{k}-5", f"hq{k}")
    for k in range(NUM_CATS):
        for i, off in enumerate(RING_PATTERN, start=1):
            a = math.radians(k * 60 - 90 + i * 10)
            kind = "roll" if off is None else "cat"
            cat = None if off is None else (k + off) % NUM_CATS
            add(f"r{k}-{i}", kind, cat, RING_RADIUS * math.cos(a), RING_RADIUS * math.sin(a))
        link(f"hq{k}", f"r{k}-1")
        for i in range(1, 5):
            link(f"r{k}-{i}", f"r{k}-{i+1}")
        link(f"r{k}-5", f"hq{(k + 1) % NUM_CATS}")
    return nodes, adj


BOARD_NODES, BOARD_ADJ = build_board()


def destinations(start: str, steps: int) -> list[str]:
    """All spaces reachable in exactly `steps` moves without revisiting any
    space during the move (the classic no-doubling-back rule)."""
    out: set[str] = set()

    def dfs(node, remaining, visited):
        if remaining == 0:
            out.add(node)
            return
        for nxt in BOARD_ADJ[node]:
            if nxt not in visited:
                dfs(nxt, remaining - 1, visited | {nxt})

    dfs(start, steps, {start})
    return sorted(out)


class GameError(Exception):
    """Raised for illegal actions; the message is safe to show the player."""


class Player:
    def __init__(self, pid: str, token: str, name: str, color: str):
        self.pid = pid
        self.token = token
        self.name = name
        self.color = color
        self.pos = "hub"
        self.wedges: set[int] = set()
        self.connected = True

    def to_dict(self):
        return {"pid": self.pid, "name": self.name, "color": self.color,
                "pos": self.pos, "wedges": sorted(self.wedges),
                "connected": self.connected}


class Game:
    """One room. Phases:
    lobby → roll → move → (question | pick_cat | final_vote) → reveal → …repeat… → gameover
    """

    def __init__(self, code: str):
        self.code = code
        self.phase = "lobby"
        self.players: list[Player] = []
        self.host_pid: str | None = None
        self.turn_idx = 0
        self.die: int | None = None
        self.dests: list[str] = []
        self.question: dict | None = None      # incl. correct_idx (server-side secret)
        self.question_cat: int | None = None   # category the pending question must be
        self.pending_wedge: int | None = None  # HQ wedge at stake this question
        self.is_final = False                  # winning question in progress
        self.reveal: dict | None = None
        self.final_votes: dict[str, int] = {}
        self.winner_pid: str | None = None
        self.nonce = 0                         # bumped every transition; guards timers

    # ── helpers ──────────────────────────────────────────────────────────────
    def _bump(self):
        self.nonce += 1

    @property
    def active(self) -> Player:
        return self.players[self.turn_idx]

    def player_by_token(self, token: str) -> Player | None:
        return next((p for p in self.players if p.token == token), None)

    def player_by_pid(self, pid: str) -> Player | None:
        return next((p for p in self.players if p.pid == pid), None)

    def acting_host_pid(self) -> str | None:
        host = self.player_by_pid(self.host_pid) if self.host_pid else None
        if host and host.connected:
            return host.pid
        return next((p.pid for p in self.players if p.connected), self.host_pid)

    def _require(self, cond, msg):
        if not cond:
            raise GameError(msg)

    # ── lobby ────────────────────────────────────────────────────────────────
    def add_player(self, token: str, name: str) -> Player:
        self._require(self.phase == "lobby", "Game already started — you can watch.")
        self._require(len(self.players) < MAX_PLAYERS, f"Room is full ({MAX_PLAYERS} players max).")
        name = (name or "").strip()[:16] or f"Player {len(self.players) + 1}"
        if any(p.name.lower() == name.lower() for p in self.players):
            name = f"{name[:13]} {len(self.players) + 1}"
        p = Player(f"p{len(self.players) + 1}", token, name,
                   PLAYER_COLORS[len(self.players)])
        self.players.append(p)
        if self.host_pid is None:
            self.host_pid = p.pid
        self._bump()
        return p

    def remove_player(self, by_pid: str, target_pid: str):
        self._require(self.phase == "lobby", "Players can only be removed in the lobby.")
        self._require(by_pid == self.acting_host_pid(), "Only the host can remove players.")
        self._require(target_pid != self.host_pid, "The host can't remove themselves.")
        self.players = [p for p in self.players if p.pid != target_pid]
        for i, p in enumerate(self.players):  # re-assign colors to stay distinct
            p.color = PLAYER_COLORS[i]
        self._bump()

    def start(self, by_pid: str):
        self._require(self.phase == "lobby", "Game already started.")
        self._require(by_pid == self.acting_host_pid(), "Only the host can start the game.")
        self._require(len(self.players) >= MIN_PLAYERS,
                      f"Need at least {MIN_PLAYERS} players.")
        self.phase = "roll"
        self.turn_idx = 0
        self._bump()

    # ── turn flow ────────────────────────────────────────────────────────────
    def roll(self, pid: str, die: int):
        self._require(self.phase == "roll", "Not time to roll.")
        self._require(pid == self.active.pid, "Not your turn.")
        self.die = die
        self.dests = destinations(self.active.pos, die)
        self.phase = "move"
        self._bump()

    def move(self, pid: str, node: str):
        self._require(self.phase == "move", "Not time to move.")
        self._require(pid == self.active.pid, "Not your turn.")
        self._require(node in self.dests, "That space isn't reachable with this roll.")
        p = self.active
        p.pos = node
        self.dests = []
        space = BOARD_NODES[node]
        self.pending_wedge = None
        self.is_final = False
        if space["kind"] == "roll":
            self.phase = "roll"                      # free extra roll
        elif space["kind"] == "hub":
            if len(p.wedges) == NUM_CATS:
                self.is_final = True
                self.final_votes = {}
                self.phase = "final_vote"            # opponents pick the category
            else:
                self.phase = "pick_cat"              # wild card — mover picks
        else:
            if space["kind"] == "hq" and space["cat"] not in p.wedges:
                self.pending_wedge = space["cat"]
            self.phase = "question"
            self.question = None                     # server fetches, then set_question
            self.question_cat = space["cat"]
        self._bump()
        return space

    def pick_category(self, pid: str, cat: int) -> None:
        """Hub wild-card: the mover picks any category; server then fetches."""
        self._require(self.phase == "pick_cat", "Not time to pick a category.")
        self._require(pid == self.active.pid, "Not your turn.")
        self._require(0 <= cat < NUM_CATS, "Unknown category.")
        self.phase = "question"
        self.question = None
        self.question_cat = cat
        self._bump()

    def vote_category(self, pid: str, cat: int):
        self._require(self.phase == "final_vote", "No category vote in progress.")
        self._require(pid != self.active.pid, "The finalist doesn't get a vote!")
        self._require(self.player_by_pid(pid) is not None, "Spectators can't vote.")
        self._require(0 <= cat < NUM_CATS, "Unknown category.")
        self.final_votes[pid] = cat
        self._bump()

    def all_votes_in(self) -> bool:
        voters = [p for p in self.players
                  if p.pid != self.active.pid and p.connected]
        return len(voters) > 0 and all(p.pid in self.final_votes for p in voters)

    def tally_final_votes(self, tiebreak: int) -> int:
        """Most-voted category; `tiebreak` (random 0..5 from the server) breaks
        ties and covers the nobody-voted case. Moves the game to `question`."""
        self._require(self.phase == "final_vote", "No category vote in progress.")
        counts = [0] * NUM_CATS
        for c in self.final_votes.values():
            counts[c] += 1
        best = max(counts)
        winners = [i for i, n in enumerate(counts) if n == best and best > 0]
        cat = winners[tiebreak % len(winners)] if winners else tiebreak % NUM_CATS
        self.phase = "question"
        self.question = None
        self.question_cat = cat
        self._bump()
        return cat

    def set_question(self, q: dict):
        self._require(self.phase == "question", "No question expected now.")
        self.question = q
        self._bump()

    def answer(self, pid: str, idx: int):
        self._require(self.phase == "question", "No question in play.")
        self._require(pid == self.active.pid, "Not your question.")
        self._require(self.question is not None, "Still drawing a card — hang on.")
        self._require(0 <= idx < len(self.question["options"]), "Pick one of the options.")
        correct = idx == self.question["correct_idx"]
        wedge = None
        if correct and self.pending_wedge is not None:
            self.active.wedges.add(self.pending_wedge)
            wedge = self.pending_wedge
        self.reveal = {"chosen_idx": idx, "correct": correct,
                       "correct_idx": self.question["correct_idx"],
                       "wedge_awarded": wedge, "was_final": self.is_final}
        if correct and self.is_final:
            self.winner_pid = pid
            self.phase = "gameover"
        else:
            self.phase = "reveal"
        self.pending_wedge = None
        self._bump()
        return correct

    def advance_after_reveal(self):
        """Called by the server's timer once players have seen the answer."""
        self._require(self.phase == "reveal", "Nothing to advance.")
        correct = self.reveal["correct"]
        self.reveal = None
        self.question = None
        self.is_final = False
        if not correct:
            self._next_turn()
        self.phase = "roll"
        self._bump()

    def skip_turn(self, by_pid: str):
        """Host escape hatch for a stuck/absent player: forfeit the turn."""
        self._require(self.phase not in ("lobby", "gameover"), "Nothing to skip.")
        self._require(by_pid == self.acting_host_pid(), "Only the host can skip a turn.")
        self.question = None
        self.reveal = None
        self.dests = []
        self.pending_wedge = None
        self.is_final = False
        self.final_votes = {}
        self._next_turn()
        self.phase = "roll"
        self._bump()

    def _next_turn(self):
        self.turn_idx = (self.turn_idx + 1) % len(self.players)

    def rematch(self, by_pid: str):
        self._require(self.phase == "gameover", "The game isn't over.")
        self._require(by_pid == self.acting_host_pid(), "Only the host can start a rematch.")
        for p in self.players:
            p.pos = "hub"
            p.wedges = set()
        self.winner_pid = None
        self.question = None
        self.reveal = None
        self.dests = []
        self.turn_idx = (self.turn_idx + 1) % len(self.players)  # rotate the opener
        self.phase = "roll"
        self._bump()

    # ── serialization ────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Public snapshot. The correct answer is only included during
        reveal/gameover so a devtools-savvy player can't peek."""
        q = None
        if self.question is not None:
            q = {"cat": self.question["cat"], "text": self.question["text"],
                 "options": self.question["options"],
                 "difficulty": self.question.get("difficulty")}
        return {
            "code": self.code,
            "phase": self.phase,
            "players": [p.to_dict() for p in self.players],
            "host": self.host_pid,
            "acting_host": self.acting_host_pid(),
            "turn": self.active.pid if self.players and self.phase != "lobby" else None,
            "die": self.die,
            "dests": self.dests,
            "question": q,
            "drawing": self.phase == "question" and self.question is None,
            "pending_wedge": self.pending_wedge,
            "is_final": self.is_final,
            "reveal": self.reveal,
            "final_votes": {pid: c for pid, c in self.final_votes.items()},
            "winner": self.winner_pid,
            "categories": CATEGORIES,
            "board": list(BOARD_NODES.values()),
        }
