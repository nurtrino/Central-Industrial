// Word banks for the word-based mini-games (server-side only — answers must
// never reach the client). Curated for "medium" solvability: common enough to
// be gettable, interesting enough to be fun.

// Anagram Race — 5–7 letter single words, no proper nouns, all distinct-ish.
export const ANAGRAM_WORDS: string[] = [
  'planet', 'rocket', 'meteor', 'cosmos', 'galaxy', 'nebula', 'gravity', 'orbit',
  'castle', 'dragon', 'wizard', 'puzzle', 'garden', 'guitar', 'jungle', 'island',
  'bridge', 'candle', 'copper', 'silver', 'diamond', 'thunder', 'breeze', 'winter',
  'summer', 'autumn', 'forest', 'meadow', 'canyon', 'desert', 'glacier', 'volcano',
  'anchor', 'compass', 'lantern', 'harbor', 'voyage', 'pirate', 'treasure', 'mystery',
  'pepper', 'walnut', 'orange', 'cherry', 'coffee', 'muffin', 'noodle', 'pickle',
  'rabbit', 'falcon', 'turtle', 'badger', 'otter', 'iguana', 'panther', 'dolphin',
  'circus', 'ticket', 'violin', 'trumpet', 'canvas', 'marble', 'crayon', 'ribbon',
  'engine', 'magnet', 'signal', 'rubber', 'button', 'socket', 'helmet', 'shovel',
  'frozen', 'bright', 'gentle', 'clever', 'hollow', 'silent', 'golden', 'velvet',
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
