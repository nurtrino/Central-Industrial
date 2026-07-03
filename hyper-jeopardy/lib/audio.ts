'use client';

let ctx: AudioContext | null = null;
let masterGain: GainNode | null = null;
let muted = false;
let activeStops: Array<() => void> = [];

function ensureCtx(): AudioContext | null {
  if (typeof window === 'undefined') return null;
  if (!ctx) {
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor();
    masterGain = ctx.createGain();
    masterGain.gain.value = muted ? 0 : 0.6;
    masterGain.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}

export function unlockAudio(): void {
  ensureCtx();
}

// === MP3 sample player =======================================================
// Plays a one-shot mp3 from /public/sounds. Returns true if it could play,
// false if the file is missing or audio is blocked — caller can fall back
// to a synthesized sting.

const sampleProbeCache = new Map<string, boolean>();
const sampleElemCache = new Map<string, HTMLAudioElement>();

function playSample(path: string, volume = 0.7): boolean {
  if (typeof window === 'undefined') return false;
  if (sampleProbeCache.get(path) === false) return false;
  try {
    let a = sampleElemCache.get(path);
    if (!a) {
      a = new Audio(path);
      a.preload = 'auto';
      sampleElemCache.set(path, a);
    }
    a.volume = muted ? 0 : volume;
    a.currentTime = 0;
    a.play().then(() => sampleProbeCache.set(path, true)).catch(() => sampleProbeCache.set(path, false));
    return true;
  } catch {
    sampleProbeCache.set(path, false);
    return false;
  }
}

export function setMuted(m: boolean): void {
  muted = m;
  if (masterGain) masterGain.gain.value = m ? 0 : 0.6;
}

export function isMuted(): boolean {
  return muted;
}

interface NoteOpts {
  freq: number;
  start: number;       // seconds offset from now
  dur: number;
  type?: OscillatorType;
  gain?: number;
  attack?: number;
  release?: number;
}

function scheduleNote({ freq, start, dur, type = 'triangle', gain = 0.3, attack = 0.01, release = 0.05 }: NoteOpts): () => void {
  const c = ensureCtx();
  if (!c || !masterGain) return () => {};
  const t0 = c.currentTime + start;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(gain, t0 + attack);
  g.gain.setValueAtTime(gain, t0 + Math.max(attack, dur - release));
  g.gain.linearRampToValueAtTime(0, t0 + dur);
  osc.connect(g).connect(masterGain);
  osc.start(t0);
  osc.stop(t0 + dur + 0.05);
  return () => {
    try { osc.stop(); } catch {}
    try { osc.disconnect(); } catch {}
    try { g.disconnect(); } catch {}
  };
}

// === Short stings ============================================================

export function playBuzzReady(): void {
  // Quick rising blip — the board says "buzzers active"
  scheduleNote({ freq: 880, start: 0, dur: 0.08, type: 'square', gain: 0.18 });
  scheduleNote({ freq: 1320, start: 0.07, dur: 0.1, type: 'square', gain: 0.18 });
}

export function playBuzzIn(): void {
  // Clack-style buzz when a player buzzes in
  scheduleNote({ freq: 220, start: 0, dur: 0.08, type: 'square', gain: 0.12 });
}

export function playCorrect(): void {
  if (playSample('/sounds/correct.mp3', 0.7)) return;
  // Fallback: two-note ascending bell
  scheduleNote({ freq: 784, start: 0, dur: 0.18, type: 'triangle', gain: 0.28 });
  scheduleNote({ freq: 1047, start: 0.15, dur: 0.28, type: 'triangle', gain: 0.28 });
}

export function playBoardFill(): void {
  // Plays when the board reveals at the start of a round
  playSample('/sounds/board-fill.mp3', 0.55);
}

// The game-start cue — Brad's laser-charge clip, in place of the traditional
// Jeopardy opening jingle. Falls back to a synthesized charge-up if the file
// is missing.
export function playGameStart(): void {
  if (playSample('/sounds/laser-charge.mp3', 0.85)) return;
  // Fallback: rising charge whine → zap
  scheduleNote({ freq: 160, start: 0, dur: 0.9, type: 'sawtooth', gain: 0.16 });
  scheduleNote({ freq: 320, start: 0.05, dur: 0.85, type: 'square', gain: 0.10 });
  scheduleNote({ freq: 1400, start: 0.95, dur: 0.3, type: 'triangle', gain: 0.22 });
}

// HYPER MODE activation sting — a bright ascending zap so phones react the
// instant a hyper cell is chosen. Used as the fallback when no laser clip
// has loaded yet.
export function playHyper(): void {
  scheduleNote({ freq: 440, start: 0.0, dur: 0.10, type: 'square', gain: 0.16 });
  scheduleNote({ freq: 660, start: 0.09, dur: 0.10, type: 'square', gain: 0.16 });
  scheduleNote({ freq: 990, start: 0.18, dur: 0.12, type: 'square', gain: 0.18 });
  scheduleNote({ freq: 1480, start: 0.29, dur: 0.28, type: 'triangle', gain: 0.22 });
  scheduleNote({ freq: 1976, start: 0.42, dur: 0.30, type: 'triangle', gain: 0.16 });
}

// === Laser clips (HYPER MODE activation) ====================================
// Every clip listed in /sounds/lasers/manifest.json is preloaded so a random
// one fires instantly when HYPER MODE activates. Regenerate the manifest with
// scripts/build-laser-manifest.mjs after adding clips.
let laserEls: HTMLAudioElement[] = [];
let laserLoadStarted = false;

export function preloadLasers(): void {
  if (laserLoadStarted || typeof window === 'undefined') return;
  laserLoadStarted = true;
  fetch('/sounds/lasers/manifest.json', { cache: 'force-cache' })
    .then((r) => r.json())
    .then((m: { clips?: string[] }) => {
      laserEls = (m.clips || []).map((name) => {
        const a = new Audio(`/sounds/lasers/${name}`);
        a.preload = 'auto';
        a.load();
        return a;
      });
    })
    .catch(() => { /* fall back to the synth sting */ });
}

// Play a random preloaded laser clip. Falls back to the synth zap if none are
// loaded yet or playback is blocked.
export function playRandomLaser(): void {
  if (!laserEls.length) { playHyper(); return; }
  const a = laserEls[Math.floor(Math.random() * laserEls.length)];
  try {
    a.currentTime = 0;
    a.volume = muted ? 0 : 0.85;
    const p = a.play();
    if (p && p.catch) p.catch(() => playHyper());
  } catch {
    playHyper();
  }
}

export function playWrong(): void {
  if (playSample('/sounds/wrong.mp3', 0.3)) return;
  // Fallback: descending sad buzz
  scheduleNote({ freq: 196, start: 0, dur: 0.45, type: 'sawtooth', gain: 0.10 });
  scheduleNote({ freq: 165, start: 0.0, dur: 0.45, type: 'sawtooth', gain: 0.08 });
}

export function playTimeUp(): void {
  // Three short blares (like the famous Jeopardy timeout buzzer "wahhh")
  scheduleNote({ freq: 247, start: 0,    dur: 0.18, type: 'square', gain: 0.12 });
  scheduleNote({ freq: 247, start: 0.22, dur: 0.18, type: 'square', gain: 0.12 });
  scheduleNote({ freq: 196, start: 0.44, dur: 0.40, type: 'square', gain: 0.12 });
}

export function playDailyDouble(): void {
  // Dramatic Daily Double fanfare — rising arpeggio, sustained chord, stinger.
  type N = { f: number; s: number; d: number; g?: number; t?: OscillatorType };
  const notes: N[] = [
    // Rising arpeggio C5 E5 G5 C6 E6 G6
    { f: 523,  s: 0.00, d: 0.10 },
    { f: 659,  s: 0.09, d: 0.10 },
    { f: 784,  s: 0.18, d: 0.10 },
    { f: 1047, s: 0.27, d: 0.10 },
    { f: 1319, s: 0.36, d: 0.10 },
    { f: 1568, s: 0.45, d: 0.18 },
    // Sustained C-major triad (chord)
    { f: 523,  s: 0.70, d: 0.55, g: 0.16, t: 'triangle' },
    { f: 659,  s: 0.70, d: 0.55, g: 0.14, t: 'triangle' },
    { f: 784,  s: 0.70, d: 0.55, g: 0.14, t: 'triangle' },
    // Final stinger up high
    { f: 1568, s: 1.30, d: 0.45, g: 0.24, t: 'triangle' },
    { f: 1976, s: 1.30, d: 0.45, g: 0.20, t: 'triangle' },
  ];
  for (const n of notes) {
    scheduleNote({ freq: n.f, start: n.s, dur: n.d, type: n.t ?? 'triangle', gain: n.g ?? 0.22 });
  }
}

export function playClueSelect(): void {
  scheduleNote({ freq: 523, start: 0, dur: 0.08, type: 'triangle', gain: 0.18 });
}

export function playRoundChange(): void {
  // Short sting when entering a new round
  const notes = [392, 523, 659, 784]; // G4 C5 E5 G5
  notes.forEach((f, i) => scheduleNote({ freq: f, start: i * 0.1, dur: 0.18, type: 'triangle', gain: 0.25 }));
}

// === Final Jeopardy "Think" theme ===========================================
// Tries /sounds/think.mp3 first; if missing, falls back to a synthesized
// 30-second loop in the spirit of game-show think music.

let thinkAudioElem: HTMLAudioElement | null = null;
let thinkProbeResult: 'unknown' | 'present' | 'absent' = 'unknown';

async function probeThinkMp3(): Promise<boolean> {
  if (thinkProbeResult !== 'unknown') return thinkProbeResult === 'present';
  try {
    const r = await fetch('/sounds/think.mp3', { method: 'HEAD' });
    thinkProbeResult = r.ok && r.headers.get('content-type')?.includes('audio') !== false ? 'present' : 'absent';
  } catch {
    thinkProbeResult = 'absent';
  }
  return thinkProbeResult === 'present';
}

interface ScheduledOsc {
  osc: OscillatorNode;
  gain: GainNode;
}

function makeVoice(c: AudioContext, type: OscillatorType, target: AudioNode): ScheduledOsc {
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  g.gain.value = 0;
  osc.connect(g).connect(target);
  return { osc, gain: g };
}

export function playThinkMusic(durationSec = 30): () => void {
  // Prefer a real mp3 if the user has dropped one in /public/sounds/think.mp3
  if (typeof window !== 'undefined') {
    probeThinkMp3().then((present) => {
      if (!present) return;
      // Only switch to mp3 if synth isn't already running for this call
      // (probe is async; on first call we may have started synth already.)
      if (thinkAudioElem) return;
      try {
        const a = new Audio('/sounds/think.mp3');
        a.loop = true;
        a.volume = muted ? 0 : 0.7;
        a.play().catch(() => {});
        thinkAudioElem = a;
        // Stop synth so we don't double up
        for (const s of activeStops.slice()) s();
        activeStops = [];
      } catch {}
    });
  }

  const c = ensureCtx();
  if (!c || !masterGain) return () => {};

  const bus = c.createGain();
  bus.gain.value = 0.9;
  bus.connect(masterGain);

  const lead = makeVoice(c, 'triangle', bus);
  const bass = makeVoice(c, 'sine', bus);
  lead.osc.start();
  bass.osc.start();

  // Melody (Hz) and rhythm (beats) for one phrase. ~120 BPM => 0.5s per beat.
  // Phrase is ~7.5 seconds, looped 4 times = 30s.
  // Notes use C-major-ish pentatonic feel.
  type N = { f: number; b: number };
  const REST = 0;
  const phrase: N[] = [
    // bar 1
    { f: 523.25, b: 0.5 }, // C5
    { f: 523.25, b: 0.5 }, // C5
    { f: 523.25, b: 0.5 }, // C5
    { f: 392.00, b: 0.5 }, // G4
    // bar 2
    { f: 523.25, b: 0.5 }, // C5
    { f: 392.00, b: 0.5 }, // G4
    { f: 523.25, b: 1.0 }, // C5 (held)
    // bar 3
    { f: 523.25, b: 0.5 }, // C5
    { f: 523.25, b: 0.5 }, // C5
    { f: 587.33, b: 0.5 }, // D5
    { f: 659.25, b: 0.5 }, // E5
    // bar 4
    { f: 587.33, b: 0.5 }, // D5
    { f: 523.25, b: 0.5 }, // C5
    { f: 440.00, b: 1.0 }, // A4 (held)
  ];

  // Bass line: walking root–fifth pattern, half notes
  const bassLine: N[] = [
    { f: 130.81, b: 1.0 }, // C3
    { f: 196.00, b: 1.0 }, // G3
    { f: 130.81, b: 1.0 }, // C3
    { f: 196.00, b: 1.0 }, // G3
    { f: 174.61, b: 1.0 }, // F3
    { f: 196.00, b: 1.0 }, // G3
    { f: 130.81, b: 2.0 }, // C3
  ];

  const SEC_PER_BEAT = 0.5;
  const phraseBeats = phrase.reduce((s, n) => s + n.b, 0); // ~7.5
  const phraseSec = phraseBeats * SEC_PER_BEAT;
  const loops = Math.ceil(durationSec / phraseSec);

  const t0 = c.currentTime + 0.05;
  const stopAt = t0 + durationSec;

  for (let loop = 0; loop < loops; loop++) {
    const loopStart = t0 + loop * phraseSec;
    if (loopStart >= stopAt) break;

    // Lead
    let cursor = loopStart;
    for (const n of phrase) {
      const dur = n.b * SEC_PER_BEAT;
      if (cursor >= stopAt) break;
      if (n.f !== REST) {
        const noteEnd = Math.min(cursor + dur, stopAt);
        lead.osc.frequency.setValueAtTime(n.f, cursor);
        lead.gain.gain.setValueAtTime(0, cursor);
        lead.gain.gain.linearRampToValueAtTime(0.18, cursor + 0.01);
        lead.gain.gain.setValueAtTime(0.18, Math.max(cursor + 0.01, noteEnd - 0.04));
        lead.gain.gain.linearRampToValueAtTime(0, noteEnd);
      }
      cursor += dur;
    }

    // Bass
    let bcursor = loopStart;
    for (const n of bassLine) {
      const dur = n.b * SEC_PER_BEAT;
      if (bcursor >= stopAt) break;
      const noteEnd = Math.min(bcursor + dur, stopAt);
      bass.osc.frequency.setValueAtTime(n.f, bcursor);
      bass.gain.gain.setValueAtTime(0, bcursor);
      bass.gain.gain.linearRampToValueAtTime(0.12, bcursor + 0.02);
      bass.gain.gain.setValueAtTime(0.12, Math.max(bcursor + 0.02, noteEnd - 0.05));
      bass.gain.gain.linearRampToValueAtTime(0, noteEnd);
      bcursor += dur;
    }
  }

  // Final "ding" at the end
  scheduleNote({ freq: 1047, start: durationSec, dur: 0.5, type: 'triangle', gain: 0.3 });

  const stop = () => {
    try {
      const now = c.currentTime;
      lead.gain.gain.cancelScheduledValues(now);
      bass.gain.gain.cancelScheduledValues(now);
      lead.gain.gain.setValueAtTime(lead.gain.gain.value, now);
      bass.gain.gain.setValueAtTime(bass.gain.gain.value, now);
      lead.gain.gain.linearRampToValueAtTime(0, now + 0.05);
      bass.gain.gain.linearRampToValueAtTime(0, now + 0.05);
      lead.osc.stop(now + 0.1);
      bass.osc.stop(now + 0.1);
    } catch {}
    activeStops = activeStops.filter(s => s !== stop);
  };
  activeStops.push(stop);

  // Auto-cleanup after duration
  setTimeout(() => stop(), (durationSec + 1) * 1000);

  return stop;
}

export function stopAllMusic(): void {
  for (const s of activeStops.slice()) s();
  activeStops = [];
  if (thinkAudioElem) {
    try { thinkAudioElem.pause(); thinkAudioElem.currentTime = 0; } catch {}
    thinkAudioElem = null;
  }
}
