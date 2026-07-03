// Marks a clue that originally depended on an image/map/video/audio clip we
// don't have — j-archive doesn't archive media, so these are unanswerable as
// text. scripts/apply-image-clue-fix.ts writes this exact string into
// question/answer for any clue it flags; the UI (ClueModal, /display, /dev)
// detects it and renders a blank box instead of the literal placeholder.
//
// Deliberately dependency-free (no fs/path) so both server code (lib/games.ts)
// and client components ('use client') can import it without pulling
// Node-only modules into the browser bundle.
export const UNAVAILABLE_CLUE_SENTINEL = '[IMAGE CLUE — UNAVAILABLE]';

export function isUnavailableClue(text: string | undefined | null): boolean {
  return text === UNAVAILABLE_CLUE_SENTINEL;
}
