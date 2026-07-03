/**
 * Blanks out the clues found by find-image-clues.ts. For each match, replaces
 * the clue's `question` (and `answer`, for safety/consistency) with an empty
 * string directly in the raw source .ts file — a surgical text replacement,
 * not a full re-serialization, so the rest of each 70k/30k-line file is left
 * byte-for-byte untouched.
 *
 * Walks matches in the same order find-image-clues.ts produced them (which
 * is file/array traversal order), and for each one does a forward-only
 * indexOf search so duplicate clue text across the file can't cause a wrong
 * match to get blanked.
 *
 * Run with: npx tsx scripts/apply-image-clue-fix.ts
 */
import fs from 'fs';

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

const matches: Match[] = JSON.parse(fs.readFileSync('scripts/.image-clue-matches.json', 'utf8'));

// build-seed.ts's validateGame() rejects any clue with an empty question/
// answer (drops the WHOLE GAME from the seed) — so the placeholder must be
// non-empty. lib/games.ts + the UI detect this exact sentinel and render an
// actual blank box instead of the literal text; see BLANK_CLUE_SENTINEL.
const SENTINEL = '[IMAGE CLUE — UNAVAILABLE]';

const FILES: Record<string, { path: string; keyStyle: 'quoted' | 'bare' }> = {
  'games-data.ts': { path: 'scripts/games-data.ts', keyStyle: 'quoted' },
  'scraped-extra.ts': { path: 'scripts/scraped-extra.ts', keyStyle: 'bare' },
};

for (const [source, { path, keyStyle }] of Object.entries(FILES)) {
  let text = fs.readFileSync(path, 'utf8');
  const theseMatches = matches.filter(m => m.source === source);

  let cursor = 0;
  let replaced = 0;
  let missed: Match[] = [];

  for (const m of theseMatches) {
    const qKey = keyStyle === 'quoted' ? '"question"' : 'question';
    const aKey = keyStyle === 'quoted' ? '"answer"' : 'answer';
    const oldQuestionLiteral = `${qKey}: ${JSON.stringify(m.text)}`;
    const newQuestionLiteral = `${qKey}: ${JSON.stringify(SENTINEL)}`;

    const idx = text.indexOf(oldQuestionLiteral, cursor);
    if (idx === -1) {
      missed.push(m);
      continue;
    }

    // Replace the question…
    text = text.slice(0, idx) + newQuestionLiteral + text.slice(idx + oldQuestionLiteral.length);
    const afterQuestion = idx + newQuestionLiteral.length;

    // …and blank the answer that immediately follows on the same clue object
    // (within the next ~400 chars, well inside one { question, answer } literal).
    const answerSearchWindow = text.slice(afterQuestion, afterQuestion + 400);
    const aKeyIdx = answerSearchWindow.indexOf(`${aKey}: "`);
    if (aKeyIdx !== -1) {
      const absStart = afterQuestion + aKeyIdx;
      // Find the closing quote of the answer value, respecting backslash escapes.
      let i = absStart + `${aKey}: "`.length;
      while (i < text.length) {
        if (text[i] === '\\') { i += 2; continue; }
        if (text[i] === '"') break;
        i++;
      }
      const fullAnswerLiteral = text.slice(absStart, i + 1); // e.g. answer: "..."
      const newAnswerLiteral = `${aKey}: ${JSON.stringify(SENTINEL)}`;
      text = text.slice(0, absStart) + newAnswerLiteral + text.slice(absStart + fullAnswerLiteral.length);
      cursor = absStart + newAnswerLiteral.length;
    } else {
      cursor = afterQuestion;
    }

    replaced++;
  }

  fs.writeFileSync(path, text);
  console.log(`${source}: replaced ${replaced}/${theseMatches.length} clues`);
  if (missed.length) {
    console.log(`  MISSED (${missed.length}):`);
    for (const m of missed) console.log(`    #${m.showNumber} ${m.round}/${m.category}/${m.index}: ${m.text}`);
  }
}
