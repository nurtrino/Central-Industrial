// Word banks for the word-based mini-games (server-side only — answers must
// never reach the client). Curated for "medium" solvability: common enough to
// be gettable, interesting enough to be fun.

// Anagram Race — 6–7 letter single words, no proper nouns. Every entry is the
// ONLY English word its letters can spell, so each scramble has exactly one
// valid solution (verified against a 370k-word dictionary; ambiguous words like
// silent→listen/tinsel, forest→foster, garden→danger were dropped).
export const ANAGRAM_WORDS: string[] = [
  'anatomy', 'autumn', 'balloon', 'banana', 'barrel', 'bicycle', 'blanket', 'bouquet',
  'bright', 'bullet', 'button', 'cannon', 'canvas', 'cherry', 'clever', 'coffee',
  'compass', 'compost', 'cookie', 'copper', 'cosmos', 'cricket', 'crystal', 'diamond',
  'emerald', 'flight', 'fossil', 'freeze', 'frozen', 'galaxy', 'garlic', 'gloves',
  'gravity', 'guitar', 'hammer', 'harbor', 'helmet', 'hollow', 'iceberg', 'island',
  'jacket', 'jaguar', 'jungle', 'lantern', 'magnet', 'makeup', 'marvel', 'meadow',
  'muffin', 'museum', 'mystery', 'noodle', 'olympic', 'panther', 'pencil', 'penguin',
  'pepper', 'phantom', 'picnic', 'pillow', 'pocket', 'poison', 'puzzle', 'pyramid',
  'rabbit', 'rainbow', 'rhythm', 'rocket', 'rubber', 'runner', 'socket', 'stomach',
  'surgeon', 'tackle', 'texture', 'thunder', 'ticket', 'tomato', 'tractor', 'trumpet',
  'vaccine', 'vampire', 'velvet', 'viking', 'violin', 'volcano', 'voyage', 'walnut',
  'whistle', 'wizard',
];

// Letter Reveal — common 5-letter words.
export const FIVE_LETTER_WORDS: string[] = [
  'crane', 'bloom', 'shine', 'quilt', 'zebra', 'lemon', 'piano', 'olive', 'globe', 'flame',
  'brick', 'cloud', 'dwarf', 'eagle', 'frost', 'grape', 'honey', 'ivory', 'jelly', 'koala',
  'llama', 'maple', 'noble', 'ocean', 'pearl', 'quart', 'robin', 'stork', 'tiger', 'ultra',
  'vivid', 'wharf', 'xenon', 'yacht', 'zesty', 'amber', 'blaze', 'chalk', 'delta', 'ember',
  'fjord', 'gecko', 'haunt', 'inlet', 'joker', 'knack', 'lunar', 'mirth', 'nifty', 'oxide',
  'prism', 'quash', 'rider', 'siren', 'tulip', 'usher', 'vault', 'wrist', 'yield', 'zonal',
  'brave', 'creek', 'drift', 'evoke', 'ferry', 'gleam', 'hatch', 'index', 'jumbo', 'kayak',
  'latch', 'mango', 'nudge', 'orbit', 'plaza', 'quirk', 'raven', 'spark', 'torch', 'vigor',
];

export function randomWord(bank: string[]): string {
  return bank[Math.floor(Math.random() * bank.length)];
}

// Scramble a word so the result differs from the original (best effort).
export function scramble(word: string): string {
  const letters = word.split('');
  for (let attempt = 0; attempt < 8; attempt++) {
    for (let i = letters.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [letters[i], letters[j]] = [letters[j], letters[i]];
    }
    const out = letters.join('');
    if (out !== word) return out;
  }
  return letters.join('');
}
