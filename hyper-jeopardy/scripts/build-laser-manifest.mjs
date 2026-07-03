// Regenerate public/sounds/lasers/manifest.json from whatever audio clips are
// in that folder. Run this after dropping the "Laser Sounds" clips in:
//   node scripts/build-laser-manifest.mjs
// The client fetches the manifest, preloads every listed clip, and plays a
// random one when HYPER MODE activates.
import { readdirSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const dir = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'sounds', 'lasers');
const AUDIO = /\.(mp3|ogg|wav|m4a|aac|webm)$/i;

const clips = readdirSync(dir)
  .filter((f) => AUDIO.test(f) && f !== 'manifest.json')
  .sort();

writeFileSync(join(dir, 'manifest.json'), JSON.stringify({ clips }, null, 2) + '\n');
console.log(`laser manifest: ${clips.length} clip(s) -> ${clips.join(', ') || '(none)'}`);
