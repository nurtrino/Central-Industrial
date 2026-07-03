// Headless driver: connect two players over the real socket layer, start a
// game, and park the shared screen (/display) in a target state so we can
// screenshot it. mode = 'board' (idle board) | 'hyper' (hyper mini-game).
import { io } from 'socket.io-client';

const URL = process.env.URL || 'http://localhost:3000';
const MODE = process.argv[2] || 'board';
const opts = { path: '/api/socket', transports: ['websocket'], forceNew: true };

const host = io(URL, opts);
const p2 = io(URL, opts);
let started = false;
let acted = false;

const log = (...a) => console.log('[drive]', ...a);

host.on('state', (s) => { onState(s); });

function onState(s) {
  if (s.phase === 'lobby' && s.players.length >= 2 && !started) {
    started = true;
    log('starting game');
    host.emit('start_game');
  }
  if (MODE === 'hyper' && s.phase === 'jeopardy' && s.cluePhase === 'idle' && !acted) {
    // Find a hyper clue and select exactly it (state carries hyperClues ids).
    const board = s.currentBoard;
    outer: for (let ci = 0; ci < board.length; ci++) {
      for (let ri = 0; ri < board[ci].clues.length; ri++) {
        if (s.hyperClues.includes(board[ci].clues[ri].id)) {
          acted = true;
          log(`selecting hyper cell cat=${ci} row=${ri} (id ${board[ci].clues[ri].id})`);
          host.emit('select_clue', { catIdx: ci, clueIdx: ri });
          break outer;
        }
      }
    }
  }
  if (s.cluePhase === 'hyper_intro') log('HYPER intro splash');
  if (s.cluePhase === 'hyper_active') log('HYPER active — mini-game:', s.activeMiniGame?.title, `(${s.activeMiniGame?.family})`);
}

host.on('connect', () => {
  log('host connected');
  host.emit('join', { name: 'NOVA', isHost: true });
  setTimeout(() => p2.emit('join', { name: 'QUASAR' }), 300);
});

p2.on('connect', () => log('p2 connected'));

// Hold the process alive so /display stays parked in the target state.
log(`mode=${MODE} — holding. Ctrl-C to exit.`);
setTimeout(() => { log('timeout, exiting'); process.exit(0); }, 60_000);
