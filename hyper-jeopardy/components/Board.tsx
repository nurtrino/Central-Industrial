'use client';
import { CategoryForPlay } from '@/lib/games';
import { GameState } from '@/lib/gameEngine';

interface Props {
  board: CategoryForPlay[];
  state: GameState;
  playerId: string | null;
  onSelectClue: (catIdx: number, clueIdx: number) => void;
  revealHyper?: boolean; // testing aid: mark the hyper (mini-game) cells
}

const VALUES_J = [200, 400, 600, 800, 1000];
const VALUES_DJ = [400, 800, 1200, 1600, 2000];

export default function Board({ board, state, playerId, onSelectClue, revealHyper }: Props) {
  const isController = state.boardController === playerId;
  const isIdle = state.cluePhase === 'idle';
  const values = state.phase === 'double_jeopardy' ? VALUES_DJ : VALUES_J;
  const hyperClues = Array.isArray(state.hyperClues) ? state.hyperClues : [];
  const hyperGames = (state.hyperGames ?? {}) as Record<number, string>;
  // per-game reveal color: blue = Anagram, red = Rapid Fire, green = Letter
  // Reveal, amber = Memory Matrix
  const HG = {
    anagram_race: { cls: 'hg-anagram', badge: 'A' },
    rapid_fire: { cls: 'hg-rapid', badge: 'R' },
    letter_reveal: { cls: 'hg-letter', badge: 'L' },
    memory_match: { cls: 'hg-memory', badge: 'M' },
  } as const;

  return (
    <div className="w-full overflow-x-auto">
      <div
        className="grid gap-1.5"
        style={{ gridTemplateColumns: `repeat(${board.length}, minmax(0, 1fr))` }}
      >
        {/* Category headers */}
        {board.map((cat, ci) => (
          <div
            key={ci}
            className="jeo-tile jeo-cat rounded-md flex items-center justify-center text-center p-2 min-h-[80px]"
          >
            <span className="jeo-headline text-white text-xs sm:text-sm uppercase tracking-wide leading-tight">
              {cat.name}
            </span>
          </div>
        ))}

        {/* Clue cells */}
        {Array.from({ length: 5 }, (_, rowIdx) =>
          board.map((cat, colIdx) => {
            const clue = cat.clues[rowIdx];
            const usedSet = state.usedClues as unknown as number[];
            const used = !clue || clue.used ||
              (Array.isArray(usedSet) ? usedSet.includes(clue.id) : false);
            const value = clue?.value || values[rowIdx];
            const canClick = isController && isIdle && !used && !!clue;
            const isHyper = !!revealHyper && !used && !!clue && hyperClues.includes(clue.id);
            const hg = isHyper ? HG[hyperGames[clue!.id] as keyof typeof HG] : undefined;

            return (
              <button
                key={`${colIdx}-${rowIdx}`}
                onClick={() => canClick && onSelectClue(colIdx, rowIdx)}
                disabled={!canClick}
                className={`
                  relative v${rowIdx + 1} rounded-md flex items-center justify-center min-h-[64px] sm:min-h-[88px] text-center transition-all duration-150
                  ${used
                    ? 'jeo-tile-used cursor-default'
                    : canClick
                      ? 'jeo-tile jeo-tile-hover cursor-pointer'
                      : 'jeo-tile cursor-not-allowed opacity-80'
                  }
                  ${isHyper ? `cell-hyper ${hg?.cls ?? ''}` : ''}
                `}
              >
                {!used && (
                  <span className="jeo-value text-2xl sm:text-4xl">
                    {value}
                  </span>
                )}
                {isHyper && hg && <span className="hyper-badge" aria-hidden>{hg.badge}</span>}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
