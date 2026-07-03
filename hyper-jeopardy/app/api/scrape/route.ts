import { NextResponse } from 'next/server';
import { getGameCount } from '@/lib/games';

export async function GET() {
  const count = getGameCount();
  return NextResponse.json({ count });
}
