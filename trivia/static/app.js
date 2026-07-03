/* WedgeQuest client — one small SPA: home → lobby → game.
 * The server is the single source of truth; every state change arrives as a
 * full `snapshot` and we re-render from it. The only client-side state is
 * identity (token), the socket, and transient animation bookkeeping. */
"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";

/* ── identity ──────────────────────────────────────────────────────────── */
function randToken() {
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
}
const token = localStorage.getItem("wq_token") || randToken();
localStorage.setItem("wq_token", token);
$("name-input").value = localStorage.getItem("wq_name") || "";

/* ── client state ──────────────────────────────────────────────────────── */
let ws = null;
let roomCode = null;
let snap = null;          // latest room snapshot
let you = null;           // your pid (null = spectator)
let closedByUser = false;
let retryDelay = 1000;
let diceBusyUntil = 0;    // hold off destination rendering while dice tumble
let pendingRender = null;

/* ── connection ────────────────────────────────────────────────────────── */
function connect(code) {
  roomCode = code.toUpperCase();
  closedByUser = false;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${roomCode}`);
  ws.onopen = () => {
    retryDelay = 1000;
    ws.send(JSON.stringify({ type: "hello", token, name: $("name-input").value }));
  };
  ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));
  ws.onclose = () => {
    if (closedByUser) return;
    setTimeout(() => connect(roomCode), retryDelay);
    retryDelay = Math.min(retryDelay * 2, 10000);
  };
  location.hash = roomCode;
}

setInterval(() => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
}, 25000);

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function handleMessage(msg) {
  if (msg.type === "snapshot") {
    snap = msg.room;
    you = msg.you;
    scheduleRender();
  } else if (msg.type === "dice") {
    rollDice(msg.value, msg.pid);
  } else if (msg.type === "error") {
    toast(msg.msg);
  } else if (msg.type === "fatal") {
    closedByUser = true;
    showView("home");
    $("home-err").textContent = msg.msg;
    location.hash = "";
  }
}

/* Delay renders that land mid-dice-tumble so the animation finishes first. */
function scheduleRender() {
  const wait = diceBusyUntil - Date.now();
  clearTimeout(pendingRender);
  if (wait > 0) pendingRender = setTimeout(render, wait);
  else render();
}

/* ── views ─────────────────────────────────────────────────────────────── */
function showView(name) {
  for (const v of ["home", "lobby", "game"])
    $(`view-${v}`).classList.toggle("hidden", v !== name);
}

function render() {
  if (!snap) return;
  if (snap.phase === "lobby") renderLobby();
  else renderGame();
}

/* ── lobby ─────────────────────────────────────────────────────────────── */
function renderLobby() {
  showView("lobby");
  hideOverlays();
  $("lobby-code").textContent = snap.code;
  const ul = $("lobby-players");
  ul.innerHTML = "";
  for (const p of snap.players) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = p.color;
    const nm = document.createElement("span");
    nm.textContent = p.name + (p.pid === you ? " (you)" : "");
    li.append(dot, nm);
    if (p.pid === snap.host) {
      const b = document.createElement("span");
      b.className = "host-badge";
      b.textContent = "HOST";
      li.append(b);
    } else if (you === snap.acting_host) {
      const k = document.createElement("button");
      k.className = "kick";
      k.textContent = "✕";
      k.onclick = () => send({ type: "kick", pid: p.pid });
      li.append(k);
    }
    ul.append(li);
  }
  const isHost = you === snap.acting_host;
  $("btn-start").classList.toggle("hidden", !isHost);
  $("btn-start").disabled = snap.players.length < 2;
  $("lobby-wait").textContent = isHost
    ? (snap.players.length < 2 ? "waiting for at least one more player…" : "everyone in? hit start!")
    : you ? "waiting for the host to start…" : "game is full — you'll be spectating";
}

/* ── game view ─────────────────────────────────────────────────────────── */
function renderGame() {
  showView("game");
  renderPlayersBar();
  renderBoard();
  renderActionBar();
  renderQuestionModal();
  renderCatModal();
  renderGameOver();
}

const catColor = (c) => snap.categories[c].color;
const catName = (c) => snap.categories[c].name;
const player = (pid) => snap.players.find((p) => p.pid === pid);
const isMyTurn = () => you && snap.turn === you;

function renderPlayersBar() {
  const bar = $("players-bar");
  bar.innerHTML = "";
  for (const p of snap.players) {
    const chip = document.createElement("div");
    chip.className = "pchip" + (p.pid === snap.turn ? " turn" : "") + (p.connected ? "" : " gone");
    chip.append(wedgePie(p), Object.assign(document.createElement("span"), {
      className: "pname",
      textContent: p.name + (p.pid === you ? " ★" : ""),
    }));
    chip.firstChild.style.borderColor = p.color;
    bar.append(chip);
  }
}

/* A little 6-sector pie showing which wedges a player holds. */
function wedgePie(p) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", 30);
  svg.setAttribute("height", 30);
  svg.setAttribute("viewBox", "-16 -16 32 32");
  svg.style.borderRadius = "50%";
  svg.style.border = `2.5px solid ${p.color}`;
  for (let c = 0; c < 6; c++) {
    const a0 = ((c * 60 - 90) * Math.PI) / 180;
    const a1 = (((c + 1) * 60 - 90) * Math.PI) / 180;
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d",
      `M0 0 L${12 * Math.cos(a0)} ${12 * Math.sin(a0)} A12 12 0 0 1 ${12 * Math.cos(a1)} ${12 * Math.sin(a1)} Z`);
    path.setAttribute("fill", p.wedges.includes(c) ? catColor(c) : "#2a3355");
    svg.append(path);
  }
  return svg;
}

/* ── the board ─────────────────────────────────────────────────────────── */
let boardBuilt = false;
let dynLayer = null;

function renderBoard() {
  const svg = $("board");
  if (!boardBuilt) {
    buildStaticBoard(svg);
    boardBuilt = true;
  }
  dynLayer.innerHTML = "";

  // destination highlights (visible to everyone; tappable only by the mover)
  if (snap.phase === "move") {
    for (const nid of snap.dests) {
      const n = nodeById(nid);
      const ring = circle(n.x, n.y, n.kind === "hq" ? 29 : n.kind === "hub" ? 41 : 20, "dest");
      dynLayer.append(ring);
      if (isMyTurn()) {
        const hit = circle(n.x, n.y, n.kind === "hq" ? 29 : n.kind === "hub" ? 41 : 20, "dest-hit");
        hit.addEventListener("click", () => send({ type: "move", node: nid }));
        dynLayer.append(hit);
      }
    }
  }

  // tokens, spread slightly when stacked on the same space
  const byPos = {};
  snap.players.forEach((p) => (byPos[p.pos] = byPos[p.pos] || []).push(p));
  for (const [pos, ps] of Object.entries(byPos)) {
    const n = nodeById(pos);
    ps.forEach((p, i) => {
      const off = tokenOffset(i, ps.length);
      const tok = circle(n.x + off[0], n.y + off[1], 11, "token");
      tok.setAttribute("fill", p.color);
      const label = text(n.x + off[0], n.y + off[1] + 4, p.name[0].toUpperCase(), "token-label");
      dynLayer.append(tok, label);
    });
  }
}

function tokenOffset(i, total) {
  if (total === 1) return [0, 0];
  const a = (i / total) * 2 * Math.PI;
  return [9 * Math.cos(a), 9 * Math.sin(a)];
}

function nodeById(id) {
  return snap.board.find((n) => n.id === id);
}

function circle(x, y, r, cls) {
  const c = document.createElementNS(SVG_NS, "circle");
  c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", r);
  if (cls) c.setAttribute("class", cls);
  return c;
}

function text(x, y, s, cls) {
  const t = document.createElementNS(SVG_NS, "text");
  t.setAttribute("x", x); t.setAttribute("y", y);
  if (cls) t.setAttribute("class", cls);
  t.textContent = s;
  return t;
}

function buildStaticBoard(svg) {
  svg.innerHTML = "";
  const stat = document.createElementNS(SVG_NS, "g");
  dynLayer = document.createElementNS(SVG_NS, "g");

  // track lines: outer ring + six spokes, drawn under the spaces
  const hqs = snap.board.filter((n) => n.kind === "hq");
  const ringR = Math.hypot(hqs[0].x, hqs[0].y);
  const ring = circle(0, 0, ringR, "track");
  ring.setAttribute("fill", "none");
  ring.setAttribute("stroke-width", 7);
  stat.append(ring);
  for (const hq of hqs) {
    const spoke = document.createElementNS(SVG_NS, "line");
    spoke.setAttribute("x1", 0); spoke.setAttribute("y1", 0);
    spoke.setAttribute("x2", hq.x); spoke.setAttribute("y2", hq.y);
    spoke.setAttribute("class", "track");
    spoke.setAttribute("stroke-width", 7);
    stat.append(spoke);
  }

  for (const n of snap.board) {
    if (n.kind === "hub") {
      // six-sector wild-card hub
      for (let c = 0; c < 6; c++) {
        const a0 = ((c * 60 - 90) * Math.PI) / 180;
        const a1 = (((c + 1) * 60 - 90) * Math.PI) / 180;
        const p = document.createElementNS(SVG_NS, "path");
        p.setAttribute("d",
          `M0 0 L${36 * Math.cos(a0)} ${36 * Math.sin(a0)} A36 36 0 0 1 ${36 * Math.cos(a1)} ${36 * Math.sin(a1)} Z`);
        p.setAttribute("fill", catColor(c));
        p.setAttribute("class", "space");
        stat.append(p);
      }
    } else if (n.kind === "roll") {
      const c = circle(n.x, n.y, 15, "space roll");
      stat.append(c, text(n.x, n.y + 6, "↻", "roll-glyph"));
    } else {
      const c = circle(n.x, n.y, n.kind === "hq" ? 24 : 15, "space" + (n.kind === "hq" ? " hq" : ""));
      c.setAttribute("fill", catColor(n.cat));
      stat.append(c);
    }
  }
  svg.append(stat, dynLayer);
}

/* ── action bar ────────────────────────────────────────────────────────── */
function renderActionBar() {
  const turnP = player(snap.turn);
  const name = turnP ? turnP.name : "";
  let status = "";
  if (snap.phase === "roll") status = isMyTurn() ? "Your roll!" : `${name} is rolling…`;
  else if (snap.phase === "move")
    status = isMyTurn() ? `You rolled ${snap.die} — tap a highlighted space` : `${name} rolled ${snap.die} and is moving…`;
  else if (snap.phase === "pick_cat") status = isMyTurn() ? "Wild card!" : `${name} landed on the hub…`;
  else if (snap.phase === "final_vote") status = "FINAL QUESTION — category vote!";
  else if (snap.phase === "question") status = isMyTurn() ? "Your question!" : `${name} is answering…`;
  else if (snap.phase === "reveal") status = "";
  else if (snap.phase === "gameover") status = `${player(snap.winner)?.name} wins!`;
  $("status-line").textContent = status;

  $("btn-roll").classList.toggle("hidden", !(snap.phase === "roll" && isMyTurn()));
  const canSkip = you === snap.acting_host && snap.turn !== you &&
    !["lobby", "gameover"].includes(snap.phase);
  $("btn-skip").classList.toggle("hidden", !canSkip);
}

$("btn-start").onclick = () => send({ type: "start" });
$("btn-roll").onclick = () => send({ type: "roll" });
$("btn-skip").onclick = () => send({ type: "skip" });

/* ── question card ─────────────────────────────────────────────────────── */
function renderQuestionModal() {
  const show = ["question", "reveal"].includes(snap.phase) ||
    (snap.phase === "gameover" && snap.reveal);
  $("question-modal").classList.toggle("hidden", !show);
  if (!show) return;

  const q = snap.question;
  const reveal = snap.reveal;
  const turnP = player(snap.turn);
  const cat = q ? q.cat : null;

  const head = $("q-head");
  head.textContent = cat != null ? catName(cat) : "…";
  head.style.background = cat != null ? catColor(cat) : "#555";

  const badge = $("q-badge");
  if (snap.is_final || (reveal && reveal.was_final)) {
    badge.textContent = "⭐ FINAL QUESTION — win it all";
    badge.classList.remove("hidden");
  } else if (snap.pending_wedge != null) {
    badge.textContent = "🧀 wedge on the line!";
    badge.classList.remove("hidden");
  } else if (reveal && reveal.wedge_awarded != null) {
    badge.textContent = `🧀 ${catName(reveal.wedge_awarded)} wedge won!`;
    badge.classList.remove("hidden");
  } else badge.classList.add("hidden");

  const opts = $("q-opts");
  opts.innerHTML = "";
  if (snap.drawing || !q) {
    $("q-text").innerHTML = "";
    opts.innerHTML = `<div class="drawing">drawing a card…</div>`;
    $("q-foot").textContent = "";
    return;
  }
  $("q-text").textContent = q.text;
  q.options.forEach((opt, i) => {
    const b = document.createElement("button");
    b.className = "opt";
    b.textContent = opt;
    if (reveal) {
      b.disabled = true;
      if (i === reveal.correct_idx) b.classList.add("correct");
      else if (i === reveal.chosen_idx) b.classList.add("wrong");
      else b.classList.add("dim");
    } else if (isMyTurn()) {
      b.onclick = () => send({ type: "answer", idx: i });
    } else {
      b.disabled = true;
    }
    opts.append(b);
  });
  const who = isMyTurn() ? "you" : turnP ? turnP.name : "";
  $("q-foot").innerHTML =
    `<span>${reveal ? (reveal.correct ? `✔ ${who} got it — roll again!` : `✘ missed — next player`) : `answering: ${who}`}</span>` +
    `<span>${q.difficulty || ""} · OpenTDB CC BY-SA</span>`;
}

/* ── category pick / final vote ────────────────────────────────────────── */
function renderCatModal() {
  const show = ["pick_cat", "final_vote"].includes(snap.phase);
  $("cat-modal").classList.toggle("hidden", !show);
  if (!show) return;
  const finalVote = snap.phase === "final_vote";
  const turnP = player(snap.turn);
  const iChoose = finalVote ? you && you !== snap.turn && player(you) : isMyTurn();

  $("cat-title").textContent = finalVote
    ? `Final question for ${turnP.name} — vote the category!`
    : (isMyTurn() ? "Hub wild card — pick any category" : `${turnP.name} is picking a category…`);

  const votes = {};
  Object.values(snap.final_votes || {}).forEach((c) => (votes[c] = (votes[c] || 0) + 1));

  const box = $("cat-opts");
  box.innerHTML = "";
  snap.categories.forEach((c, i) => {
    const b = document.createElement("button");
    b.className = "catbtn";
    b.style.background = c.color;
    b.innerHTML = `<span>${c.name}</span>` +
      (finalVote && votes[i] ? `<span class="votes">${"●".repeat(votes[i])}</span>` : "");
    if (iChoose) b.onclick = () => send({ type: finalVote ? "vote" : "pick_cat", cat: i });
    else b.disabled = true;
    box.append(b);
  });
  const myVote = snap.final_votes && you in snap.final_votes;
  $("cat-note").textContent = finalVote
    ? (you === snap.turn ? "your opponents are choosing… good luck"
       : myVote ? "vote in — waiting for the others" : "pick the category they must answer")
    : "";
}

/* ── game over ─────────────────────────────────────────────────────────── */
function renderGameOver() {
  const show = snap.phase === "gameover" && !snap.reveal;
  $("gameover-modal").classList.toggle("hidden", !show);
  if (!show) {
    if (snap.phase === "gameover" && snap.reveal) {
      // let the final reveal breathe, then swap to the trophy screen
      setTimeout(() => { if (snap.phase === "gameover") { snap.reveal = null; render(); } }, 3500);
    }
    return;
  }
  const w = player(snap.winner);
  $("win-text").textContent = w ? `${w.name} wins the game!` : "Game over!";
  $("btn-rematch").classList.toggle("hidden", you !== snap.acting_host);
}
$("btn-rematch").onclick = () => send({ type: "rematch" });

function hideOverlays() {
  for (const id of ["question-modal", "cat-modal", "gameover-modal", "dice-overlay"])
    $(id).classList.add("hidden");
}

/* ── dice ──────────────────────────────────────────────────────────────── */
/* Cube faces: value → resting orientation that shows the face to the camera. */
const FACE_ROT = {
  1: [0, 0], 2: [-90, 0], 3: [0, -90], 4: [0, 90], 5: [90, 0], 6: [0, 180],
};

function buildCube() {
  const cube = $("cube");
  cube.innerHTML = "";
  const place = {
    1: "translateZ(55px)", 6: "rotateY(180deg) translateZ(55px)",
    3: "rotateY(90deg) translateZ(55px)", 4: "rotateY(-90deg) translateZ(55px)",
    2: "rotateX(90deg) translateZ(55px)", 5: "rotateX(-90deg) translateZ(55px)",
  };
  const pipCells = {
    1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8],
  };
  for (let v = 1; v <= 6; v++) {
    const f = document.createElement("div");
    f.className = "face";
    f.style.transform = place[v];
    for (let cell = 0; cell < 9; cell++) {
      const span = document.createElement("span");
      if (pipCells[v].includes(cell)) span.className = "pip";
      f.append(span);
    }
    cube.append(f);
  }
}
buildCube();

function rollDice(value, pid) {
  const p = player(pid);
  $("dice-label").textContent = p ? (pid === you ? "you roll…" : `${p.name} rolls…`) : "";
  const overlay = $("dice-overlay");
  const cube = $("cube");
  overlay.classList.remove("hidden");
  diceBusyUntil = Date.now() + 2100;

  const [rx, ry] = FACE_ROT[value];
  cube.classList.remove("tumbling");
  cube.style.transform = `rotateX(${rx - 540 - Math.floor(Math.random() * 2) * 360}deg) ` +
                         `rotateY(${ry - 720}deg) rotateZ(-360deg)`;
  void cube.offsetWidth;                      // flush, then animate to rest
  cube.classList.add("tumbling");
  cube.style.transform = `rotateX(${rx}deg) rotateY(${ry}deg) rotateZ(0deg)`;

  setTimeout(() => {
    $("dice-label").textContent = `${p && pid === you ? "you" : p ? p.name : ""} rolled ${value}!`;
  }, 1400);
  setTimeout(() => overlay.classList.add("hidden"), 2050);
}

/* ── toast ─────────────────────────────────────────────────────────────── */
let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}

/* ── home wiring ───────────────────────────────────────────────────────── */
function saveName() {
  localStorage.setItem("wq_name", $("name-input").value.trim());
}

$("btn-create").onclick = async () => {
  saveName();
  if (!$("name-input").value.trim()) return ($("home-err").textContent = "enter your name first");
  $("home-err").textContent = "";
  try {
    const r = await fetch("/api/rooms", { method: "POST" });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "could not create a room");
    connect(data.code);
  } catch (e) {
    $("home-err").textContent = e.message;
  }
};

$("btn-join").onclick = () => {
  saveName();
  const code = $("code-input").value.trim().toUpperCase();
  if (!$("name-input").value.trim()) return ($("home-err").textContent = "enter your name first");
  if (code.length !== 4) return ($("home-err").textContent = "room codes are 4 letters");
  $("home-err").textContent = "";
  connect(code);
};

// arriving via a shared link (…/#ABCD) pre-fills the room code
if (location.hash.length === 5) $("code-input").value = location.hash.slice(1).toUpperCase();
