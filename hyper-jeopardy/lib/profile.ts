'use client';

// All canonical account data (name, avatar, wins) now lives on the server in
// data/accounts.json — see lib/accounts.ts. The only thing each device keeps
// locally is a pointer to the account it last played as, so a returning
// visitor lands on their own face by default.

const LAST_USED_KEY = 'jeo_last_account_id';

export function getLastUsedAccountId(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(LAST_USED_KEY);
}

export function setLastUsedAccountId(id: string): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(LAST_USED_KEY, id);
}

export function clearLastUsedAccountId(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(LAST_USED_KEY);
}

// Center-crop + downscale an uploaded image to a square ~128 px JPEG. Keeps
// the data URL small enough (~3–10 KB) to ship via socket state on every
// account update without bloating broadcasts.
export async function fileToAvatarDataURL(file: File, max = 128): Promise<string> {
  const dataUrl: string = await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result as string);
    fr.onerror = reject;
    fr.readAsDataURL(file);
  });
  const img: HTMLImageElement = await new Promise((resolve, reject) => {
    const i = new Image();
    i.onload = () => resolve(i);
    i.onerror = reject;
    i.src = dataUrl;
  });
  const side = Math.min(img.width, img.height);
  const sx = (img.width - side) / 2;
  const sy = (img.height - side) / 2;
  const target = Math.min(max, side);
  const canvas = document.createElement('canvas');
  canvas.width = target;
  canvas.height = target;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('no 2d context');
  ctx.drawImage(img, sx, sy, side, side, 0, 0, target, target);
  return canvas.toDataURL('image/jpeg', 0.82);
}
