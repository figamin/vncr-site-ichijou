'use client';

import type { VNExternalDetail } from '@/lib/vndb-stats-api';

interface VNCrossDbStatsProps {
  vndbRating?: number | null;
  vndbVotecount?: number;
  vndbAvg?: number | null;
  external?: VNExternalDetail | null;
}

export function VNCrossDbStats({
  vndbRating,
  vndbVotecount,
  vndbAvg,
  external,
}: VNCrossDbStatsProps) {
  const egs = external?.egs;
  const bgm = external?.bangumi;

  if (!egs && !bgm) return null;

  const fav = bgm?.favorite;
  const favTotal = fav
    ? fav.done + fav.doing + fav.wish + fav.on_hold + fav.dropped
    : 0;

  return (
    <section className="vn-sec p-4 sm:p-5">
      <div className="vn-sec-head">
        <h2 className="vn-sec-title">Cross-Database Reception</h2>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="border-t border-[color:var(--rule)] pt-3">
          <span className="fig-label">VNDB</span>
          <div className="vn-num mt-1 text-xl font-medium text-[color:var(--ink)]">
            {vndbRating != null ? vndbRating.toFixed(2) : '—'}
            <span className="text-xs text-[color:var(--text-faint)] font-normal"> / 10</span>
          </div>
          <div className="text-xs text-[color:var(--nezu)] mt-1 space-y-0.5">
            {vndbVotecount != null && (
              <div><span className="vn-num">{vndbVotecount.toLocaleString()}</span> votes</div>
            )}
            {vndbAvg != null && (
              <div>Raw avg: <span className="vn-num">{vndbAvg.toFixed(2)}</span></div>
            )}
          </div>
        </div>

        {egs && (
          <div className="border-t border-[color:var(--rule)] pt-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="fig-label">ErogameScape</span>
              <a
                href={egs.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[11px] text-[color:var(--ai)] hover:underline"
              >
                Open ↗
              </a>
            </div>
            <div className="vn-num mt-1 text-xl font-medium text-[color:var(--ink)]">
              {egs.median != null ? egs.median : '—'}
              <span className="text-xs text-[color:var(--text-faint)] font-normal"> / 100 median</span>
            </div>
            <div className="text-xs text-[color:var(--nezu)] mt-1 space-y-0.5">
              {egs.count != null && (
                <div>
                  <span className="vn-num">{egs.count.toLocaleString()}</span> votes
                  {egs.average != null && <> · avg <span className="vn-num">{egs.average}</span></>}
                  {egs.stdev != null && <> · σ <span className="vn-num">{egs.stdev}</span></>}
                </div>
              )}
              {egs.total_reviews > 0 && (
                <div>
                  <span className="vn-num">{egs.hitokoto_count.toLocaleString()}</span> hitokoto ·{' '}
                  <span className="vn-num">{egs.memo_count.toLocaleString()}</span> memos
                </div>
              )}
              {(egs.play_count > 0 || egs.giveup_count > 0) && (
                <div>
                  <span className="vn-num">{egs.play_count.toLocaleString()}</span> played ·{' '}
                  <span className="vn-num">{egs.giveup_count.toLocaleString()}</span> dropped
                </div>
              )}
            </div>
          </div>
        )}

        {bgm && (
          <div className="border-t border-[color:var(--rule)] pt-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="fig-label">Bangumi</span>
              <a
                href={bgm.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[11px] text-[color:var(--ai)] hover:underline"
              >
                Open ↗
              </a>
            </div>
            <div className="vn-num mt-1 text-xl font-medium text-[color:var(--ink)]">
              {bgm.score != null ? bgm.score.toFixed(1) : '—'}
              <span className="text-xs text-[color:var(--text-faint)] font-normal"> / 10</span>
              {bgm.rank != null && (
                <span className="text-xs text-[color:var(--nezu)] ml-2 font-normal">
                  #{bgm.rank.toLocaleString()}
                </span>
              )}
            </div>
            <div className="text-xs text-[color:var(--nezu)] mt-1 space-y-0.5">
              {bgm.total_votes > 0 && (
                <div><span className="vn-num">{bgm.total_votes.toLocaleString()}</span> votes</div>
              )}
              {fav && favTotal > 0 && (
                <div>
                  <span className="vn-num">{fav.done.toLocaleString()}</span> done ·{' '}
                  <span className="vn-num">{fav.wish.toLocaleString()}</span> wish ·{' '}
                  <span className="vn-num">{fav.doing.toLocaleString()}</span> playing
                </div>
              )}
              {fav && (fav.on_hold > 0 || fav.dropped > 0) && (
                <div>
                  <span className="vn-num">{fav.on_hold.toLocaleString()}</span> hold ·{' '}
                  <span className="vn-num">{fav.dropped.toLocaleString()}</span> dropped
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
