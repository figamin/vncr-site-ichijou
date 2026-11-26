'use client';

import { useState } from 'react';
import { Music } from 'lucide-react';
import type { VNExternalDetail } from '@/lib/vndb-stats-api';

interface VNExternalCommunityProps {
  external: VNExternalDetail | null | undefined;
}

const RELATION_TYPE_LABELS: Record<string, string> = {
  anime: 'Anime',
  book: 'Book / Manga',
  music: 'Soundtrack / Music',
  real: 'Live Action',
  game: 'Game',
  other: 'Related',
};

export function VNExternalCommunity({ external }: VNExternalCommunityProps) {
  const [showSpoilerPovs, setShowSpoilerPovs] = useState(false);
  const [showAllPovs, setShowAllPovs] = useState(false);

  const egs = external?.egs;
  const bgm = external?.bangumi;

  const povs = egs?.povs ?? [];
  const music = egs?.music ?? [];
  const bgmTags = bgm?.tags ?? [];
  const bgmMetaTags = bgm?.meta_tags ?? [];
  const bgmRelations = bgm?.relations ?? [];

  if (
    povs.length === 0 &&
    music.length === 0 &&
    bgmTags.length === 0 &&
    bgmRelations.length === 0
  ) {
    return null;
  }

  const filteredPovs = povs.filter((p) => showSpoilerPovs || !p.spoiler);
  const spoilerPovCount = povs.filter((p) => p.spoiler).length;
  const visiblePovs = showAllPovs ? filteredPovs : filteredPovs.slice(0, 15);

  return (
    <div className="space-y-4">
      {povs.length > 0 && (
        <section className="vn-sec p-4 sm:p-5">
          <div className="vn-sec-head">
            <div className="flex items-baseline gap-2">
              <h2 className="vn-sec-title">ErogameScape POVs</h2>
              <span className="vn-num text-sm text-[color:var(--text-faint)]">
                {filteredPovs.length}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {spoilerPovCount > 0 && (
                <button
                  type="button"
                  onClick={() => setShowSpoilerPovs((v) => !v)}
                  aria-pressed={showSpoilerPovs}
                  className={`tab${showSpoilerPovs ? ' tab--on' : ''}`}
                >
                  <span>
                    <span className="hidden sm:inline">{showSpoilerPovs ? 'Hide' : 'Show'} </span>
                    spoilers
                  </span>
                  <span className="tab-count">{spoilerPovCount}</span>
                </button>
              )}
              {egs?.url && (
                <a
                  href={egs.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="sec-more"
                >
                  EGS <span aria-hidden>↗</span>
                </a>
              )}
            </div>
          </div>

          {filteredPovs.length > 0 ? (
            <>
              <div className="flex flex-wrap gap-1.5">
                {visiblePovs.map((pov) => (
                  <span
                    key={pov.id}
                    className={`vn-chip${pov.spoiler ? ' vn-chip--held' : ''}`}
                    title={`Rank ${pov.rank} · ${pov.count.toLocaleString()} votes${pov.spoiler ? ' (Spoiler)' : ''}`}
                  >
                    <span className="font-mono text-[10px] font-bold text-[color:var(--ai)] mr-1">
                      {pov.rank}
                    </span>
                    <span>{pov.title}</span>
                    <span className="vn-num text-[11px] text-[color:var(--text-faint)] ml-1">
                      {pov.count.toLocaleString()}
                    </span>
                    {pov.spoiler && <span className="font-mono text-xs ml-0.5">!</span>}
                  </span>
                ))}
              </div>
              {filteredPovs.length > 15 && !showAllPovs && (
                <button
                  type="button"
                  onClick={() => setShowAllPovs(true)}
                  className="sec-more mt-3"
                >
                  Show all {filteredPovs.length} POVs <span aria-hidden>→</span>
                </button>
              )}
            </>
          ) : (
            <p className="text-xs text-[color:var(--nezu)]">
              All {spoilerPovCount} POVs for this title are marked as spoilers.
            </p>
          )}
        </section>
      )}

      {bgmTags.length > 0 && bgm && (
        <section className="vn-sec p-4 sm:p-5">
          <div className="vn-sec-head">
            <div className="flex items-baseline gap-2">
              <h2 className="vn-sec-title">Bangumi Community Tags</h2>
              <span className="vn-num text-sm text-[color:var(--text-faint)]">
                {bgmTags.length}
              </span>
            </div>
            <a
              href={bgm.url}
              target="_blank"
              rel="noopener noreferrer"
              className="sec-more"
            >
              Bangumi <span aria-hidden>↗</span>
            </a>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {bgmMetaTags.map((mt) => (
              <span
                key={`meta-${mt}`}
                className="px-1.5 py-0.5 rounded-xs border border-[color:var(--rule)] font-mono text-[10px] uppercase tracking-[0.06em] text-[color:var(--nezu)]"
              >
                {mt}
              </span>
            ))}
            {bgmTags.map((tag) => (
              <span key={tag.name} className="vn-chip">
                <span>{tag.name}</span>
                <span className="vn-num text-[11px] text-[color:var(--text-faint)] ml-1">
                  {tag.count.toLocaleString()}
                </span>
              </span>
            ))}
          </div>
        </section>
      )}

      {music.length > 0 && (
        <section className="vn-sec p-4 sm:p-5">
          <div className="vn-sec-head">
            <div className="flex items-center gap-2">
              <Music className="w-4 h-4 text-[color:var(--ai)]" aria-hidden />
              <h2 className="vn-sec-title">Theme Songs &amp; Music</h2>
            </div>
            {egs?.url && (
              <a
                href={egs.url}
                target="_blank"
                rel="noopener noreferrer"
                className="sec-more"
              >
                EGS <span aria-hidden>↗</span>
              </a>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {music.map((track) => (
              <div
                key={`${track.category}-${track.id}`}
                className="flex items-baseline gap-2 border-t border-[color:var(--rule)] pt-2 text-sm"
              >
                <span className="px-1.5 py-0.5 rounded-xs border border-[color:var(--rule)] font-mono text-[10px] uppercase tracking-[0.08em] text-[color:var(--ai)] shrink-0">
                  {track.category}
                </span>
                <div className="min-w-0">
                  <div className="text-[color:var(--ink)] font-medium truncate" title={track.name}>
                    {track.name}
                  </div>
                  {track.singers.length > 0 && (
                    <div className="text-xs text-[color:var(--nezu)] truncate">
                      {track.singers.join(', ')}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {bgmRelations.length > 0 && (
        <section className="vn-sec p-4 sm:p-5">
          <div className="vn-sec-head">
            <div className="flex items-baseline gap-2">
              <h2 className="vn-sec-title">Cross-Media Adaptations</h2>
              <span className="vn-num text-sm text-[color:var(--text-faint)]">
                {bgmRelations.length}
              </span>
            </div>
            {bgm?.url && (
              <a
                href={bgm.url}
                target="_blank"
                rel="noopener noreferrer"
                className="sec-more"
              >
                Bangumi <span aria-hidden>↗</span>
              </a>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {bgmRelations.map((rel) => {
              const displayTitle = rel.name_cn || rel.name;
              return (
                <a
                  key={rel.id}
                  href={rel.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex items-start justify-between gap-3 border-t border-[color:var(--rule)] pt-2.5 hover:border-[color:var(--ai)] transition-colors"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <span className="px-1.5 py-0.5 rounded-xs border border-[color:var(--rule)] font-mono text-[10px] uppercase tracking-[0.06em] text-[color:var(--nezu)]">
                        {RELATION_TYPE_LABELS[rel.type] || rel.type}
                      </span>
                      {rel.date && (
                        <span className="vn-num text-[11px] text-[color:var(--text-faint)]">
                          {rel.date.slice(0, 4)}
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-[color:var(--ink)] group-hover:text-[color:var(--ai)] truncate">
                      {displayTitle}
                    </div>
                    {rel.name_cn && rel.name !== rel.name_cn && (
                      <div className="text-xs text-[color:var(--nezu)] truncate">
                        {rel.name}
                      </div>
                    )}
                  </div>
                  <div className="text-right shrink-0">
                    {rel.score != null && (
                      <div className="vn-num text-sm font-medium text-[color:var(--ink)]">
                        {rel.score.toFixed(1)}
                      </div>
                    )}
                    {rel.rank != null && (
                      <div className="vn-num text-[11px] text-[color:var(--text-faint)]">
                        #{rel.rank.toLocaleString()}
                      </div>
                    )}
                  </div>
                </a>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
