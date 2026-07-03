"""
Question supply for WedgeQuest — Open Trivia Database (opentdb.com) client.

OTDB is a free, community-maintained trivia API (CC BY-SA 4.0 — the UI carries
attribution). It asks clients to stay under roughly one request per 5 seconds
per IP, so this module never fetches on the hot path if it can help it:

  • one queue of ready-to-serve questions per wedge category
  • a background refill loop tops up the emptiest queue, one API call at a
    time, honouring a global rate limiter
  • queues persist to a JSON file across restarts (best effort)
  • if OTDB is down/slow and a queue runs dry, a small built-in emergency set
    keeps the game moving (facts in our own wording)

Each wedge category maps to a POOL of OTDB category ids so thin OTDB
categories (e.g. Art) are backed by siblings (Books, Musicals).

Set OTDB_OFFLINE=1 to skip the network entirely (tests / airplane dev).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import time
from collections import deque

import httpx

OTDB_API = "https://opentdb.com/api.php"
OTDB_TOKEN = "https://opentdb.com/api_token.php"

# wedge category index (see game.CATEGORIES) → OTDB category ids
POOLS = {
    0: [22],           # Geography
    1: [11, 12, 14],   # Entertainment: Film, Music, Television
    2: [23],           # History
    3: [25, 10, 13],   # Art, Books, Musicals & Theatre
    4: [17, 27],       # Science & Nature, Animals
    5: [21, 16],       # Sports, Board Games
}

LOW_WATER = 25        # refill a queue when it drops below this
BATCH = 20            # questions per API call
MIN_GAP = 5.3         # seconds between OTDB calls (their guidance is ~5s)
CACHE_PATH = os.environ.get("QCACHE_PATH",
                            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "qcache.json"))
OFFLINE = os.environ.get("OTDB_OFFLINE", "") == "1"

# Emergency questions (own phrasing, well-known facts) — used only when a
# queue is empty and OTDB can't be reached in time.
FALLBACK = {
    0: [("Which river is the longest in South America?",
         "The Amazon", ["The Paraná", "The Orinoco", "The Magdalena"]),
        ("Canberra is the capital city of which country?",
         "Australia", ["New Zealand", "Canada", "South Africa"]),
        ("Which U.S. state has the longest coastline?",
         "Alaska", ["California", "Florida", "Hawaii"]),
        ("Mount Kilimanjaro is located in which country?",
         "Tanzania", ["Kenya", "Ethiopia", "Uganda"])],
    1: [("Which 1977 film introduced audiences to Luke Skywalker?",
         "Star Wars", ["Close Encounters of the Third Kind", "Blade Runner", "Alien"]),
        ("Which band recorded the album 'Abbey Road'?",
         "The Beatles", ["The Rolling Stones", "The Kinks", "The Who"]),
        ("Which TV series is set in the fictional town of Hawkins, Indiana?",
         "Stranger Things", ["Riverdale", "Twin Peaks", "Dark"]),
        ("Who directed the film 'Jaws'?",
         "Steven Spielberg", ["George Lucas", "Martin Scorsese", "Ridley Scott"])],
    2: [("In which year did the Berlin Wall fall?",
         "1989", ["1985", "1991", "1979"]),
        ("Who was the first President of the United States?",
         "George Washington", ["Thomas Jefferson", "John Adams", "Benjamin Franklin"]),
        ("The ancient city of Rome was traditionally founded on how many hills?",
         "Seven", ["Five", "Nine", "Three"]),
        ("Which ship famously sank on its maiden voyage in 1912?",
         "RMS Titanic", ["RMS Lusitania", "HMS Britannic", "SS Andrea Doria"])],
    3: [("Who painted the Mona Lisa?",
         "Leonardo da Vinci", ["Michelangelo", "Raphael", "Sandro Botticelli"]),
        ("Who wrote the novel 'Moby-Dick'?",
         "Herman Melville", ["Nathaniel Hawthorne", "Mark Twain", "Edgar Allan Poe"]),
        ("Which playwright wrote 'Romeo and Juliet'?",
         "William Shakespeare", ["Christopher Marlowe", "Ben Jonson", "Oscar Wilde"]),
        ("The novel '1984' was written by which author?",
         "George Orwell", ["Aldous Huxley", "Ray Bradbury", "H.G. Wells"])],
    4: [("What is the chemical symbol for gold?",
         "Au", ["Ag", "Gd", "Go"]),
        ("Which planet in our solar system is known as the Red Planet?",
         "Mars", ["Venus", "Jupiter", "Mercury"]),
        ("What gas do plants primarily absorb for photosynthesis?",
         "Carbon dioxide", ["Oxygen", "Nitrogen", "Hydrogen"]),
        ("How many bones are in the adult human body?",
         "206", ["186", "226", "312"])],
    5: [("How many players are on the field per side in a soccer match?",
         "11", ["9", "10", "12"]),
        ("In which sport would you perform a slam dunk?",
         "Basketball", ["Volleyball", "Tennis", "Handball"]),
        ("How many squares are on a standard chessboard?",
         "64", ["49", "81", "100"]),
        ("The Summer Olympics are held every how many years?",
         "Four", ["Two", "Three", "Five"])],
}


def _b64(s: str) -> str:
    return base64.b64decode(s).decode("utf-8")


class QuestionBank:
    def __init__(self):
        self.queues: dict[int, deque] = {c: deque() for c in POOLS}
        self.seen: set[str] = set()
        self.token: str | None = None
        self._next_call = 0.0
        self._lock = asyncio.Lock()
        self._dirty = False
        self._load_cache()

    # ── public API ───────────────────────────────────────────────────────────
    async def get(self, cat: int) -> dict:
        """Pop a ready question for wedge category `cat`. Falls back to the
        built-in emergency set rather than blocking the game for long."""
        if self.queues[cat]:
            self._dirty = True
            return self.queues[cat].popleft()
        if not OFFLINE:
            try:
                await asyncio.wait_for(self._refill(cat), timeout=12)
            except (asyncio.TimeoutError, httpx.HTTPError):
                pass
            if self.queues[cat]:
                self._dirty = True
                return self.queues[cat].popleft()
        text, right, wrong = random.choice(FALLBACK[cat])
        return self._pack(cat, text, right, list(wrong), "medium")

    async def refill_loop(self):
        """Background task: keep every queue above LOW_WATER, one polite API
        call at a time."""
        if OFFLINE:
            return
        while True:
            try:
                cat = min(POOLS, key=lambda c: len(self.queues[c]))
                if len(self.queues[cat]) < LOW_WATER:
                    await self._refill(cat)
                if self._dirty:
                    self._save_cache()
            except Exception:
                pass  # a bad cycle must never kill the loop
            await asyncio.sleep(MIN_GAP + 1)

    def counts(self) -> dict[int, int]:
        return {c: len(q) for c, q in self.queues.items()}

    # ── OTDB plumbing ────────────────────────────────────────────────────────
    def _pack(self, cat, text, right, wrong, difficulty):
        options = wrong + [right]
        random.shuffle(options)
        return {"cat": cat, "text": text, "options": options,
                "correct_idx": options.index(right), "difficulty": difficulty}

    async def _wait_turn(self):
        now = time.monotonic()
        if now < self._next_call:
            await asyncio.sleep(self._next_call - now)
        self._next_call = time.monotonic() + MIN_GAP

    async def _get_token(self, client) -> str | None:
        await self._wait_turn()
        r = await client.get(OTDB_TOKEN, params={"command": "request"}, timeout=10)
        data = r.json()
        return data.get("token") if data.get("response_code") == 0 else None

    async def _refill(self, cat: int):
        """One rate-limited fetch into `cat`'s queue. Serialized by a lock so
        concurrent games can't stampede the API."""
        async with self._lock:
            if self.queues[cat] and len(self.queues[cat]) >= LOW_WATER:
                return
            async with httpx.AsyncClient() as client:
                if self.token is None:
                    try:
                        self.token = await self._get_token(client)
                    except httpx.HTTPError:
                        self.token = None
                otdb_id = random.choice(POOLS[cat])
                for amount, use_token in ((BATCH, True), (10, False)):
                    await self._wait_turn()
                    params = {"amount": amount, "category": otdb_id,
                              "type": "multiple", "encode": "base64"}
                    if use_token and self.token:
                        params["token"] = self.token
                    r = await client.get(OTDB_API, params=params, timeout=15)
                    data = r.json()
                    rc = data.get("response_code")
                    if rc == 0:
                        self._ingest(cat, data.get("results", []))
                        return
                    if rc in (3, 4):       # token missing/exhausted → drop it, retry
                        self.token = None
                        continue
                    if rc == 5:            # rate limited → back off, let the loop retry
                        self._next_call = time.monotonic() + MIN_GAP * 2
                        return
                    # rc == 1 (too few questions) falls through to the smaller retry

    def _ingest(self, cat: int, results: list[dict]):
        added = 0
        for item in results:
            try:
                text = _b64(item["question"]).strip()
                right = _b64(item["correct_answer"]).strip()
                wrong = [_b64(w).strip() for w in item["incorrect_answers"]]
                difficulty = _b64(item.get("difficulty", "")) or None
            except Exception:
                continue
            if not text or not right or len(wrong) != 3 or text in self.seen:
                continue
            self.seen.add(text)
            self.queues[cat].append(self._pack(cat, text, right, wrong, difficulty))
            added += 1
        if added:
            self._dirty = True

    # ── persistence (best effort — Render disks are ephemeral, still helps) ──
    def _load_cache(self):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for c, items in raw.items():
                ci = int(c)
                if ci in self.queues:
                    for q in items:
                        if q["text"] not in self.seen:
                            self.seen.add(q["text"])
                            self.queues[ci].append(q)
        except Exception:
            pass

    def _save_cache(self):
        try:
            tmp = CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({str(c): list(q) for c, q in self.queues.items()}, f)
            os.replace(tmp, CACHE_PATH)
            self._dirty = False
        except Exception:
            pass
