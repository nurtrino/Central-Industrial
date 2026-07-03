"""Engine tests: board integrity + a full scripted rulebook walk-through."""
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import (BOARD_ADJ, BOARD_NODES, NUM_CATS, Game, GameError,
                  destinations)


# ── board topology ───────────────────────────────────────────────────────────
def test_board_counts():
    assert len(BOARD_NODES) == 67  # 1 hub + 30 spoke + 6 hq + 30 ring
    kinds = {}
    for n in BOARD_NODES.values():
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    assert kinds == {"hub": 1, "cat": 54, "hq": 6, "roll": 6}


def test_each_category_has_equal_spaces():
    per_cat = [0] * NUM_CATS
    for n in BOARD_NODES.values():
        if n["kind"] in ("cat", "hq"):
            per_cat[n["cat"]] += 1
    assert per_cat == [10] * NUM_CATS  # 5 spoke + 4 ring + 1 hq each


def test_adjacency_symmetric_and_connected():
    for a, nbrs in BOARD_ADJ.items():
        assert len(nbrs) == len(set(nbrs))
        for b in nbrs:
            assert a in BOARD_ADJ[b]
    seen, stack = set(), ["hub"]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(BOARD_ADJ[n])
    assert seen == set(BOARD_NODES)


def test_hub_degree_and_hq_degree():
    assert len(BOARD_ADJ["hub"]) == 6           # one per spoke
    for k in range(6):
        assert len(BOARD_ADJ[f"hq{k}"]) == 3    # spoke + two ring arcs


def test_destinations_always_exist():
    for node in BOARD_NODES:
        for die in range(1, 7):
            assert destinations(node, die), f"no moves from {node} with a {die}"


def test_no_doubling_back():
    # From hub with a 2 you must be 2 spaces down some spoke — never back on hub.
    dests = destinations("hub", 2)
    assert "hub" not in dests
    assert set(dests) == {f"s{k}-2" for k in range(6)}


def test_exact_count_into_hub():
    # From s0-3 a roll of 3 reaches the hub (3 steps in); a 2 cannot.
    assert "hub" in destinations("s0-3", 3)
    assert "hub" not in destinations("s0-3", 2)


def test_can_cut_through_hub():
    # Spoke-to-spoke through the center in one move: 2 steps in, then out.
    assert "s3-1" in destinations("s0-2", 3)
    assert "s3-2" in destinations("s0-2", 4)


# ── scripted game ────────────────────────────────────────────────────────────
def make_game(n=2):
    g = Game("TEST")
    for i in range(n):
        g.add_player(f"tok{i}", f"P{i}")
    g.start("p1")
    return g


def q_for(cat, correct_first=True):
    return {"cat": cat, "text": "q?", "options": ["right", "a", "b", "c"],
            "correct_idx": 0, "difficulty": "easy"}


def test_lobby_rules():
    g = Game("TEST")
    p1 = g.add_player("t1", "Alice")
    assert g.host_pid == p1.pid
    with pytest.raises(GameError):
        g.start(p1.pid)                          # needs 2+
    g.add_player("t2", "Bob")
    with pytest.raises(GameError):
        g.start("p2")                            # only host starts
    g.start(p1.pid)
    assert g.phase == "roll"
    with pytest.raises(GameError):
        g.add_player("t3", "Late")               # no joining mid-game


def test_room_capacity_and_name_dedupe():
    g = Game("TEST")
    for i in range(6):
        g.add_player(f"t{i}", "Same")
    assert len({p.name for p in g.players}) == 6
    with pytest.raises(GameError):
        g.add_player("t7", "Seventh")


def test_wrong_answer_passes_turn():
    g = make_game()
    g.roll("p1", 1)
    dest = next(d for d in g.dests if BOARD_NODES[d]["kind"] == "cat")
    g.move("p1", dest)
    assert g.phase == "question" and g.question_cat == BOARD_NODES[dest]["cat"]
    g.set_question(q_for(g.question_cat))
    assert g.answer("p1", 1) is False
    assert g.phase == "reveal"
    g.advance_after_reveal()
    assert g.phase == "roll" and g.active.pid == "p2"


def test_correct_answer_rolls_again():
    g = make_game()
    g.roll("p1", 1)
    dest = next(d for d in g.dests if BOARD_NODES[d]["kind"] == "cat")
    g.move("p1", dest)
    g.set_question(q_for(g.question_cat))
    assert g.answer("p1", 0) is True
    g.advance_after_reveal()
    assert g.phase == "roll" and g.active.pid == "p1"     # same player again


def test_roll_again_space():
    g = make_game()
    g.active.pos = "hq0"
    g.roll("p1", 3)
    assert "r0-3" in g.dests                              # middle of the arc
    g.move("p1", "r0-3")
    assert g.phase == "roll" and g.active.pid == "p1"     # free roll, no question


def test_hq_awards_wedge():
    g = make_game()
    g.active.pos = "s1-4"
    g.roll("p1", 2)
    g.move("p1", "hq1")
    assert g.pending_wedge == 1
    g.set_question(q_for(1))
    g.answer("p1", 0)
    assert 1 in g.active.wedges
    assert g.reveal["wedge_awarded"] == 1
    # landing there again with the wedge held: question, but nothing at stake
    g.advance_after_reveal()
    g.active.pos = "s1-4"
    g.roll("p1", 2)
    g.move("p1", "hq1")
    assert g.pending_wedge is None


def test_hub_wildcard_before_six_wedges():
    g = make_game()
    g.active.pos = "s2-1"
    g.roll("p1", 1)
    g.move("p1", "hub")
    assert g.phase == "pick_cat"
    with pytest.raises(GameError):
        g.pick_category("p2", 3)                          # not their turn
    g.pick_category("p1", 3)
    assert g.phase == "question" and g.question_cat == 3


def test_final_flow_win():
    g = make_game(3)
    g.active.wedges = set(range(6))
    g.active.pos = "s0-1"
    g.roll("p1", 1)
    g.move("p1", "hub")
    assert g.phase == "final_vote" and g.is_final
    with pytest.raises(GameError):
        g.vote_category("p1", 0)                          # finalist can't vote
    g.vote_category("p2", 4)
    assert not g.all_votes_in()
    g.vote_category("p3", 4)
    assert g.all_votes_in()
    cat = g.tally_final_votes(tiebreak=0)
    assert cat == 4 and g.phase == "question"
    g.set_question(q_for(4))
    g.answer("p1", 0)
    assert g.phase == "gameover" and g.winner_pid == "p1"
    assert g.reveal["was_final"]


def test_final_flow_miss_continues_game():
    g = make_game()
    g.active.wedges = set(range(6))
    g.active.pos = "s0-1"
    g.roll("p1", 1)
    g.move("p1", "hub")
    g.vote_category("p2", 2)
    g.tally_final_votes(tiebreak=0)
    g.set_question(q_for(2))
    g.answer("p1", 3)                                     # miss
    assert g.phase == "reveal" and g.winner_pid is None
    g.advance_after_reveal()
    assert g.phase == "roll" and g.active.pid == "p2"


def test_vote_tally_tiebreak_and_no_votes():
    g = make_game(4)
    g.active.wedges = set(range(6))
    g.active.pos = "s0-1"
    g.roll("p1", 1)
    g.move("p1", "hub")
    g.vote_category("p2", 1)
    g.vote_category("p3", 5)
    assert g.tally_final_votes(tiebreak=1) == 5           # tie → tiebreak picks
    # nobody voted at all → tiebreak is the category
    g2 = make_game()
    g2.active.wedges = set(range(6))
    g2.active.pos = "s0-1"
    g2.roll("p1", 1)
    g2.move("p1", "hub")
    assert g2.tally_final_votes(tiebreak=3) == 3


def test_move_validation():
    g = make_game()
    g.roll("p1", 2)
    with pytest.raises(GameError):
        g.move("p1", "hub")                               # not reachable with a 2
    with pytest.raises(GameError):
        g.move("p2", g.dests[0])                          # not their turn


def test_skip_and_host_powers():
    g = make_game(3)
    g.roll("p1", 3)
    with pytest.raises(GameError):
        g.skip_turn("p2")                                 # only host skips
    g.skip_turn("p1")
    assert g.phase == "roll" and g.active.pid == "p2"
    # host disconnects → next connected player becomes acting host
    g.players[0].connected = False
    assert g.acting_host_pid() == "p2"
    g.skip_turn("p2")
    assert g.active.pid == "p3"


def test_rematch_resets():
    g = make_game()
    g.active.wedges = set(range(6))
    g.active.pos = "s0-1"
    g.roll("p1", 1)
    g.move("p1", "hub")
    g.vote_category("p2", 0)
    g.tally_final_votes(0)
    g.set_question(q_for(0))
    g.answer("p1", 0)
    assert g.phase == "gameover"
    g.rematch("p1")
    assert g.phase == "roll"
    assert all(p.pos == "hub" and not p.wedges for p in g.players)
    assert g.active.pid == "p2"                           # opener rotates


def test_snapshot_hides_answer_until_reveal():
    g = make_game()
    g.roll("p1", 1)
    dest = next(d for d in g.dests if BOARD_NODES[d]["kind"] == "cat")
    g.move("p1", dest)
    g.set_question(q_for(g.question_cat))
    snap = g.to_dict()
    assert "correct_idx" not in snap["question"]
    g.answer("p1", 0)
    assert g.to_dict()["reveal"]["correct_idx"] == 0
