'use client';

import { useState, useEffect } from 'react';
import Link from '@/components/Link';

import { useTitlePreference } from '@/lib/title-preference';
import type { ExternalRatingsSummary } from '@/lib/vndb-stats-api';
import { lengthLabels, platformNames, formatReleaseDate, formatUpdatedAt } from './vn-utils';

// The timestamp arrives without an offset, which Date reads as local time. Anchoring it to
// UTC keeps a server and a reader in another zone measuring from the same instant.
const asUtcInstant = (value: string) =>
  /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;

interface VNSidebarProps {
  developers?: Array<{ id: string; name: string; original?: string }>;
  released?: string;
  length?: number;
  platforms?: string[];
  languages?: string[];
  links?: Array<{ site: string; url: string; label: string }>;
  shops?: Array<{ site: string; url: string; label: string }>;
  updatedAt?: string;
  external?: ExternalRatingsSummary | null;
}

export function VNSidebar({
  developers,
  released,
  length,
  platforms,
  languages,
  links,
  shops,
  updatedAt,
  external,
}: VNSidebarProps) {
  const { preference } = useTitlePreference();
  const [showAllPlatforms, setShowAllPlatforms] = useState(false);
  const [showAllLanguages, setShowAllLanguages] = useState(false);
  const [showAllLinks, setShowAllLinks] = useState(false);
  const [showAllShops, setShowAllShops] = useState(false);
  const lengthInfo = length ? lengthLabels[length] : null;
  const formattedDate = released ? formatReleaseDate(released) : null;
  // This string is measured against the clock at render time, and the page is served from a
  // cache that outlives several of its buckets. The value rendered on the server is left in
  // place through hydration and replaced once the reader's own clock is available.
  const [clientUpdatedAt, setClientUpdatedAt] = useState<string | null>(null);
  useEffect(() => {
    setClientUpdatedAt(updatedAt ? formatUpdatedAt(asUtcInstant(updatedAt)) : null);
  }, [updatedAt]);
  const formattedUpdatedAt = clientUpdatedAt ?? (updatedAt ? formatUpdatedAt(asUtcInstant(updatedAt)) : null);

  return (
    <div className="space-y-3">
      {/* Metadata items: compact grid */}
      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2.5 text-sm items-baseline">
        {developers && developers.length > 0 && (
          <>
            <span className="fig-label">Developer</span>
            <div className="text-[color:var(--ink)]">
              {developers.map((dev, i) => {
                const displayName = (preference === 'romaji' && dev.original)
                  ? dev.original
                  : dev.name;
                return (
                  <span key={dev.id}>
                    {i > 0 && ', '}
                    <Link
                      href={`/stats/producer/${dev.id}`}
                      className="hover:text-[color:var(--ai)] hover:underline transition-colors"
                    >
                      {displayName}
                    </Link>
                  </span>
                );
              })}
            </div>
          </>
        )}

        {formattedDate && (
          <>
            <span className="fig-label">Released</span>
            <div className="vn-num text-[color:var(--ink)]">{formattedDate}</div>
          </>
        )}

        {lengthInfo && (
          <>
            <span className="fig-label">Length</span>
            <div className="text-[color:var(--ink)]">
              {lengthInfo.label}
              <span className="vn-num text-[color:var(--text-faint)] ml-1">({lengthInfo.hours})</span>
            </div>
          </>
        )}

        {external?.egs?.playtime_median != null && (
          <>
            <span className="fig-label">EGS Playtime</span>
            <div className="vn-num text-[color:var(--ink)]">
              ~{external.egs.playtime_median}h median
              {external.egs.fun_time_median != null && (
                <span className="text-[color:var(--text-faint)] ml-1 text-xs">
                  (hooks ~{external.egs.fun_time_median}h)
                </span>
              )}
            </div>
          </>
        )}

        {external?.egs?.genre && (
          <>
            <span className="fig-label">JP Genre</span>
            <div className="text-[color:var(--ink)] text-xs leading-snug">
              {external.egs.genre}
            </div>
          </>
        )}

        {external?.bangumi?.name_cn && (
          <>
            <span className="fig-label">CN Title</span>
            <div className="text-[color:var(--ink)] text-xs leading-snug">
              {external.bangumi.name_cn}
            </div>
          </>
        )}

        {external?.egs?.trial_url && (
          <>
            <span className="fig-label">Trial</span>
            <div>
              <a
                href={external.egs.trial_url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="text-xs text-[color:var(--ai)] hover:underline"
              >
                Official demo / trial <span aria-hidden>↗</span>
              </a>
            </div>
          </>
        )}

        {platforms && platforms.length > 0 && (
          <div className="col-span-2">
            <span className="fig-label">Platforms</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {(showAllPlatforms ? platforms : platforms.slice(0, 5)).map(p => (
                <span
                  key={p}
                  className="px-1.5 py-0.5 rounded-xs border border-[color:var(--rule)] font-mono text-[10px] uppercase tracking-[0.08em] text-[color:var(--nezu)]"
                >
                  {platformNames[p] || p}
                </span>
              ))}
              {!showAllPlatforms && platforms.length > 5 && (
                <button
                  onClick={() => setShowAllPlatforms(true)}
                  className="vn-num text-xs text-[color:var(--ai)] hover:underline hit-24"
                >
                  +{platforms.length - 5}
                </button>
              )}
            </div>
          </div>
        )}

        {languages && languages.length > 0 && (
          <div className="col-span-2">
            <span className="fig-label">Languages</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {(showAllLanguages ? languages : languages.slice(0, 8)).map(lang => (
                <span
                  key={lang}
                  className="px-1.5 py-0.5 rounded-xs border border-[color:var(--rule)] font-mono text-[10px] uppercase tracking-[0.08em] text-[color:var(--nezu)]"
                >
                  {lang}
                </span>
              ))}
              {!showAllLanguages && languages.length > 8 && (
                <button
                  onClick={() => setShowAllLanguages(true)}
                  className="vn-num text-xs text-[color:var(--ai)] hover:underline hit-24"
                >
                  +{languages.length - 8}
                </button>
              )}
            </div>
          </div>
        )}

      </div>

      {links && links.length > 0 && (
        <div className="pt-3 border-t border-[color:var(--rule)]">
          <span className="fig-label">Links</span>
          <p className="text-xs leading-relaxed mt-1">
            {(showAllLinks ? links : links.slice(0, 5)).map((link, i, arr) => (
              <span key={`${link.site}-${i}`}>
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[color:var(--ai)] hover:underline transition-colors"
                >
                  {link.label}
                </a>
                {i < arr.length - 1 && <span className="text-[color:var(--text-faint)]">, </span>}
              </span>
            ))}
            {!showAllLinks && links.length > 5 && (
              <button
                onClick={() => setShowAllLinks(true)}
                className="ml-0.5 text-[color:var(--ai)] hover:underline transition-colors"
              >
                +{links.length - 5} more
              </button>
            )}
          </p>
        </div>
      )}

      {shops && shops.length > 0 && (
        <div className="mt-3">
          <span className="fig-label">Shops</span>
          <p className="text-xs leading-relaxed mt-1">
            {(showAllShops ? shops : shops.slice(0, 5)).map((shop, i, arr) => (
              <span key={`${shop.site}-${i}`}>
                <a
                  href={shop.url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  className="text-[color:var(--ai)] hover:underline transition-colors"
                >
                  {shop.label}
                </a>
                {i < arr.length - 1 && <span className="text-[color:var(--text-faint)]">, </span>}
              </span>
            ))}
            {!showAllShops && shops.length > 5 && (
              <button
                onClick={() => setShowAllShops(true)}
                className="ml-0.5 text-[color:var(--ai)] hover:underline transition-colors"
              >
                +{shops.length - 5} more
              </button>
            )}
          </p>
        </div>
      )}

      {formattedUpdatedAt && (
        <p
          suppressHydrationWarning
          className="mt-3 font-mono text-[10px] uppercase tracking-[0.08em] text-[color:var(--text-faint)]"
        >
          Updated {formattedUpdatedAt.toLowerCase()}
        </p>
      )}

    </div>
  );
}

// ─── Rating Bar ───

export function RatingArc({ rating, votecount }: { rating: number; votecount: number }) {
  const progress = Math.max(0, Math.min((rating - 1) / 9, 1)) * 100;

  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="vn-num text-2xl font-medium text-[color:var(--ink)]">
          {rating.toFixed(2)}
        </span>
        <span className="vn-num text-xs text-[color:var(--text-faint)]">
          / 10
        </span>
        <span className="vn-num ml-auto text-xs text-[color:var(--nezu)]">
          {votecount.toLocaleString()} votes
        </span>
      </div>
      {/* Amber carries the live figure. Where the fill stops reports the rating, so it is one
          colour at every value rather than a band the reader has to decode. */}
      <div
        className="mt-1.5 h-1.5 bg-[color:var(--surface-inset)] border border-[color:var(--rule)]"
        role="progressbar"
        aria-valuenow={rating}
        aria-valuemin={1}
        aria-valuemax={10}
        aria-label={`Rating: ${rating.toFixed(2)} out of 10`}
      >
        <div
          className="h-full bg-[color:var(--kohaku)]"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

export function ExternalRatingsBar({ external }: { external?: ExternalRatingsSummary | null }) {
  const egs = external?.egs;
  const bgm = external?.bangumi;
  const hasEgs = egs && egs.median != null;
  const hasBgm = bgm && bgm.score != null;

  if (!hasEgs && !hasBgm) return null;

  return (
    <div className="mt-2.5 pt-2 border-t border-[color:var(--rule)] space-y-1.5 text-xs">
      {hasEgs && (
        <div className="flex items-baseline gap-2">
          <a
            href={egs.url}
            target="_blank"
            rel="noopener noreferrer"
            className="fig-label hover:text-[color:var(--ai)] hover:underline"
            title="ErogameScape median score (0–100)"
          >
            ErogameScape
          </a>
          <span className="vn-num font-medium text-[color:var(--ink)] ml-auto">
            {egs.median}
            <span className="text-[color:var(--text-faint)] font-normal"> / 100</span>
          </span>
          {egs.count != null && (
            <span className="vn-num text-[11px] text-[color:var(--nezu)]">
              ({egs.count.toLocaleString()})
            </span>
          )}
        </div>
      )}

      {hasBgm && (
        <div className="flex items-baseline gap-2">
          <a
            href={bgm.url}
            target="_blank"
            rel="noopener noreferrer"
            className="fig-label hover:text-[color:var(--ai)] hover:underline"
            title="Bangumi community score (1–10)"
          >
            Bangumi
          </a>
          <span className="vn-num font-medium text-[color:var(--ink)] ml-auto">
            {bgm.score!.toFixed(1)}
            <span className="text-[color:var(--text-faint)] font-normal"> / 10</span>
          </span>
          {bgm.rank != null ? (
            <span className="vn-num text-[11px] text-[color:var(--nezu)]">
              (#{bgm.rank.toLocaleString()})
            </span>
          ) : bgm.votes != null ? (
            <span className="vn-num text-[11px] text-[color:var(--nezu)]">
              ({bgm.votes.toLocaleString()})
            </span>
          ) : null}
        </div>
      )}

      {external?.consensus_score != null && (
        <div className="flex items-baseline gap-2 pt-1 border-t border-dashed border-[color:var(--rule)]">
          <span className="fig-label">Consensus</span>
          <span className="vn-num font-medium text-[color:var(--ink)] ml-auto">
            {external.consensus_score.toFixed(2)}
            <span className="text-[color:var(--text-faint)] font-normal"> / 10</span>
          </span>
        </div>
      )}
    </div>
  );
}
