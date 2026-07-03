/**
 * Sweeps GAMES + EXTRA_GAMES for clues that are "obviously image/audio based"
 * — i.e. the clue text itself only makes sense with an accompanying picture,
 * map, video, or audio clip that we don't have (j-archive doesn't archive
 * media; see AGENTS.md). Prints every match for review. Run with:
 *   npx tsx scripts/find-image-clues.ts
 */
import { GAMES, type GameData, type ClueData } from './games-data';
import { EXTRA_GAMES } from './scraped-extra';

// Deictic phrases only make sense pointing at unavailable media. Word
// boundaries + "here/above/below" keep this from flagging plain descriptive
// prose like "was pictured with a robe" (a biographical fact, no image
// needed to answer).
const IMAGE_PATTERNS: RegExp[] = [
  /\bseen here\b/i,
  /\bshown here\b/i,
  /\bpictured here\b/i,
  /\bdepicted here\b/i,
  /\bshown above\b/i,
  /\bshown below\b/i,
  /\bpictured above\b/i,
  /\bpictured below\b/i,
  /\bin (this|the) (picture|photo|photograph|image|illustration|diagram|cartoon|drawing|painting|map)\b/i,
  /\bthis (picture|photo|photograph|image|map|diagram) shows\b/i,
  /\bthe (item|object|man|woman|actor|actress|animal|creature|flag|logo|symbol) (shown|pictured|seen)\b/i,
];

const AUDIO_PATTERNS: RegExp[] = [
  /\bheard here\b/i,
  /\bjust heard\b/i,
  /\bthe clip (you )?(just )?heard\b/i,
  /\bthe (song|tune|piece|music) (you )?(just )?heard\b/i,
];

interface Match {
  source: string;
  showNumber: number;
  round: string;
  category: string;
  index: number;
  field: 'question' | 'answer';
  text: string;
  kind: 'image' | 'audio';
}

function classify(text: string): 'image' | 'audio' | null {
  if (!text) return null;
  for (const re of IMAGE_PATTERNS) if (re.test(text)) return 'image';
  for (const re of AUDIO_PATTERNS) if (re.test(text)) return 'audio';
  return null;
}

function sweep(games: GameData[], source: string): Match[] {
  const out: Match[] = [];
  for (const g of games) {
    const rounds: [string, { category: string; clues: ClueData[] }[]][] = [
      ['jeopardy', g.jeopardy],
      ['doubleJeopardy', g.doubleJeopardy],
    ];
    for (const [roundName, cats] of rounds) {
      cats.forEach((cat, ci) => {
        cat.clues.forEach((clue, cli) => {
          for (const field of ['question', 'answer'] as const) {
            const kind = classify(clue[field]);
            if (kind) {
              out.push({
                source, showNumber: g.showNumber, round: roundName,
                category: cat.category, index: cli, field, text: clue[field], kind,
              });
            }
          }
        });
      });
    }
    // Final Jeopardy too
    for (const field of ['question', 'answer'] as const) {
      const kind = classify(g.finalJeopardy[field]);
      if (kind) {
        out.push({
          source, showNumber: g.showNumber, round: 'final', category: g.finalJeopardy.category,
          index: 0, field, text: g.finalJeopardy[field], kind,
        });
      }
    }
  }
  return out;
}

const all = [...sweep(GAMES, 'games-data.ts'), ...sweep(EXTRA_GAMES, 'scraped-extra.ts')];

const images = all.filter(m => m.kind === 'image');
const audio = all.filter(m => m.kind === 'audio');

console.log(`Total games scanned: ${GAMES.length} (games-data.ts) + ${EXTRA_GAMES.length} (scraped-extra.ts)`);
console.log(`Image-based matches: ${images.length}`);
console.log(`Audio-based matches: ${audio.length}`);
console.log('');
console.log('=== IMAGE MATCHES ===');
for (const m of images) {
  console.log(`[${m.source} #${m.showNumber} ${m.round}/${m.category}/${m.index}/${m.field}] ${m.text}`);
}
console.log('');
console.log('=== AUDIO MATCHES ===');
for (const m of audio) {
  console.log(`[${m.source} #${m.showNumber} ${m.round}/${m.category}/${m.index}/${m.field}] ${m.text}`);
}

// Dump machine-readable list for the apply step.
import fs from 'fs';
fs.writeFileSync(
  'scripts/.image-clue-matches.json',
  JSON.stringify(all, null, 2),
);
console.log(`\nWrote ${all.length} matches to scripts/.image-clue-matches.json`);
