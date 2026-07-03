"""
WedgeQuest server — rooms, WebSockets, and the orchestration around game.py.

FastAPI + native WebSockets, one process (in-memory rooms — keep the Render
service at a single instance). The engine (game.py) owns the rules; this file
owns IO: dice RNG, question fetching (questions.py), phase timers, broadcast.

    GET  /              → the app (static/index.html)
    POST /api/rooms     → {"code": "ABCD"}  create a room
    GET  /healthz       → ok (Render health check)
    WS   /ws/{code}     → game protocol (JSON messages)

Client → server: hello{token,name} · start · roll · move{node} · pick_cat{cat}
                 · vote{cat} · answer{idx} · skip · kick{pid} · rematch · ping
Server → client: snapshot{you,room} · dice{pid,value} · error{msg}
                 · fatal{msg} (then close) · pong
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from game import Game, GameError
from questions import QuestionBank

BASE = os.path.dirname(os.path.abspath(__file__))
REVEAL_SECS = float(os.environ.get("REVEAL_SECS", "5"))
VOTE_SECS = float(os.environ.get("VOTE_SECS", "30"))
ROOM_IDLE_SECS = 60 * 60          # empty + idle rooms are collected after 1h
ROOM_MAX_AGE = 24 * 3600
MAX_ROOMS = 200
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"   # no I/L/O — unambiguous on phones


class Room:
    def __init__(self, code: str):
        self.game = Game(code)
        self.sockets: dict[WebSocket, str | None] = {}   # ws → pid (None = spectator)
        self.created = time.monotonic()
        self.last_active = time.monotonic()

    def touch(self):
        self.last_active = time.monotonic()


rooms: dict[str, Room] = {}
bank = QuestionBank()
app = FastAPI(title="WedgeQuest")


# ── broadcast / snapshots ─────────────────────────────────────────────────────
async def _send(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False


async def broadcast(room: Room):
    snap = room.game.to_dict()
    dead = []
    for ws, pid in list(room.sockets.items()):
        if not await _send(ws, {"type": "snapshot", "you": pid, "room": snap}):
            dead.append(ws)
    for ws in dead:
        await _drop_socket(room, ws)


async def broadcast_event(room: Room, payload: dict):
    for ws in list(room.sockets):
        await _send(ws, payload)


async def _drop_socket(room: Room, ws: WebSocket):
    pid = room.sockets.pop(ws, None)
    if pid and pid not in room.sockets.values():
        p = room.game.player_by_pid(pid)
        if p:
            p.connected = False
            await broadcast(room)


# ── phase timers (all guarded by the game nonce so a skip/rematch cancels) ───
def schedule(coro):
    asyncio.get_running_loop().create_task(coro)


async def reveal_timer(room: Room, nonce: int):
    await asyncio.sleep(REVEAL_SECS)
    g = room.game
    if g.nonce == nonce and g.phase == "reveal":
        g.advance_after_reveal()
        await broadcast(room)


async def vote_timer(room: Room, nonce: int):
    await asyncio.sleep(VOTE_SECS)
    g = room.game
    if g.nonce == nonce and g.phase == "final_vote":
        await finish_vote(room)


async def finish_vote(room: Room):
    g = room.game
    g.tally_final_votes(secrets.randbelow(6))
    await broadcast(room)
    schedule(fetch_question(room, g.nonce))


async def fetch_question(room: Room, nonce: int):
    g = room.game
    cat = g.question_cat
    if cat is None:
        return
    q = await bank.get(cat)
    if g.nonce == nonce and g.phase == "question" and g.question is None:
        g.set_question(q)
        await broadcast(room)


async def after_move(room: Room):
    """Kick off whatever the landing demands: a question fetch or vote timer."""
    g = room.game
    if g.phase == "question" and g.question is None:
        schedule(fetch_question(room, g.nonce))
    elif g.phase == "final_vote":
        schedule(vote_timer(room, g.nonce))


# ── HTTP ─────────────────────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz():
    return {"ok": True, "rooms": len(rooms), "questions": bank.counts()}


@app.post("/api/rooms")
async def create_room():
    if len(rooms) >= MAX_ROOMS:
        return JSONResponse({"error": "Server is full, try again later."}, status_code=503)
    for _ in range(50):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        if code not in rooms:
            rooms[code] = Room(code)
            return {"code": code}
    return JSONResponse({"error": "Could not allocate a room code."}, status_code=500)


@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


# ── WebSocket protocol ───────────────────────────────────────────────────────
@app.websocket("/ws/{code}")
async def ws_endpoint(ws: WebSocket, code: str):
    await ws.accept()
    room = rooms.get(code.upper())
    if room is None:
        await _send(ws, {"type": "fatal", "msg": "No such room — check the code."})
        await ws.close()
        return
    g = room.game
    pid: str | None = None
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            room.touch()

            if kind == "ping":
                await _send(ws, {"type": "pong"})
                continue

            if kind == "hello":
                token = str(msg.get("token", ""))[:64]
                existing = g.player_by_token(token) if token else None
                if existing:
                    pid = existing.pid
                    existing.connected = True
                elif g.phase == "lobby":
                    try:
                        pid = g.add_player(token, str(msg.get("name", ""))).pid
                    except GameError as e:
                        await _send(ws, {"type": "error", "msg": str(e)})
                        pid = None       # room full → stay as spectator
                # else: game in progress → spectator
                room.sockets[ws] = pid
                await broadcast(room)
                continue

            if ws not in room.sockets:
                await _send(ws, {"type": "error", "msg": "Say hello first."})
                continue

            try:
                if kind == "start":
                    g.start(pid)
                    await broadcast(room)
                elif kind == "roll":
                    die = secrets.randbelow(6) + 1     # proper RNG, server-side
                    g.roll(pid, die)
                    await broadcast_event(room, {"type": "dice", "pid": pid, "value": die})
                    await broadcast(room)
                elif kind == "move":
                    g.move(pid, str(msg.get("node", "")))
                    await broadcast(room)
                    await after_move(room)
                elif kind == "pick_cat":
                    g.pick_category(pid, int(msg.get("cat", -1)))
                    await broadcast(room)
                    schedule(fetch_question(room, g.nonce))
                elif kind == "vote":
                    g.vote_category(pid, int(msg.get("cat", -1)))
                    if g.all_votes_in():
                        await finish_vote(room)
                    else:
                        await broadcast(room)
                elif kind == "answer":
                    g.answer(pid, int(msg.get("idx", -1)))
                    await broadcast(room)
                    if g.phase == "reveal":
                        schedule(reveal_timer(room, g.nonce))
                elif kind == "skip":
                    g.skip_turn(pid)
                    await broadcast(room)
                elif kind == "kick":
                    g.remove_player(pid, str(msg.get("pid", "")))
                    await broadcast(room)
                elif kind == "rematch":
                    g.rematch(pid)
                    await broadcast(room)
                else:
                    await _send(ws, {"type": "error", "msg": f"Unknown message: {kind}"})
            except GameError as e:
                await _send(ws, {"type": "error", "msg": str(e)})
            except (TypeError, ValueError):
                await _send(ws, {"type": "error", "msg": "Malformed message."})
    except WebSocketDisconnect:
        pass
    finally:
        await _drop_socket(room, ws)


# ── background upkeep ────────────────────────────────────────────────────────
async def room_gc():
    while True:
        await asyncio.sleep(300)
        now = time.monotonic()
        for code, room in list(rooms.items()):
            empty_and_idle = not room.sockets and now - room.last_active > ROOM_IDLE_SECS
            if empty_and_idle or now - room.created > ROOM_MAX_AGE:
                rooms.pop(code, None)


@app.on_event("startup")
async def startup():
    schedule(bank.refill_loop())
    schedule(room_gc())
