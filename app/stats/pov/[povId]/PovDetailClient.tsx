'use client';

import { useEffect, useState, use, useRef, useCallback, useMemo } from 'react';
import { useSearchParams, usePathname } from 'next/navigation';
import Link from '@/components/Link';
import { ArrowLeft, ExternalLink, RefreshCw, ChevronRight } from 'lucide-react';
import { Pagination, PaginationSkeleton } from '@/components/browse/Pagination';
import { SimpleSelect } from '@/components/browse/SimpleSelect';
import {
  vndbStatsApi,
  PovStatsData,
  PovVN,
  SimilarPov,
  PovRelatedTag,
} from '@/lib/vndb-stats-api';
import { StatsSummaryCard } from '@/components/stats/StatsSummaryCard';
import { ScoreDistributionChart } from '@/components/stats/ScoreDistributionChart';
import { ReleaseYearChart } from '@/components/stats/ReleaseYearChart';
import { LengthChart } from '@/components/stats/LengthChart';
import { AgeRatingChart } from '@/components/stats/AgeRatingChart';
import { PovDetailTabs, PovTabId } from '@/components/stats/PovDetailTabs';
import { LoadingScreen } from '@/components/LoadingScreen';
import { consumePendingScroll } from '@/components/ScrollToTop';
import { getProxiedImageUrl } from '@/lib/vndb-image-cache';
import { useTitlePreference, getDisplayTitle } from '@/lib/title-preference';
import { LanguageFilter, LanguageFilterValue } from '@/components/stats/LanguageFilter';
import { sortTagsByWeight } from '@/lib/weighted-score-utils';
import { LastUpdated } from '@/components/stats/LastUpdated';
import { NSFWImage } from '@/components/NSFWImage';
import { useImageRetry } from '@/hooks/useImageRetry';
import { preloadVNImages, addRetryKey } from '@/lib/preload-images';

const VALID_TABS: PovTabId[] = ['summary', 'novels', 'similar-povs', 'related-tags'];

const TAG_CATEGORY_LABELS: Record<string, string> = {
  cont: 'Content',
  ero: 'Sexual Content',
  tech: 'Technical',
};

interface PageProps {
  params: Promise<{ povId: string }>;
}

export default function PovDetailClient({ params }: PageProps) {
  const resolvedParams = use(params);
  const povId = resolvedParams.povId;

  const searchParams = useSearchParams();
  const pathname = usePathname();
  const tabFromUrl = searchParams.get('tab') as PovTabId | null;
  const pageFromUrl = searchParams.get('page');
  const initialPage = pageFromUrl ? Math.max(1, parseInt(pageFromUrl, 10) || 1) : 1;

  const [stats, setStats] = useState<PovStatsData | null>(null);
  const [vns, setVns] = useState<PovVN[]>([]);
  const [similarPovs, setSimilarPovs] = useState<SimilarPov[]>([]);
  const [relatedTags, setRelatedTags] = useState<PovRelatedTag[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingTab, setIsLoadingTab] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshBlockedUntil, setRefreshBlockedUntil] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<PovTabId>(
    tabFromUrl && VALID_TABS.includes(tabFromUrl) ? tabFromUrl : 'summary'
  );
  const [languageFilter, setLanguageFilter] = useState<LanguageFilterValue>('ja');
  const [rankFilter, setRankFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<string>('pov_votes');
  const [showFullComment, setShowFullComment] = useState(false);
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [totalPages, setTotalPages] = useState(1);
  const [totalVns, setTotalVns] = useState(0);

  const updateUrl = useCallback(
    (tab: PovTabId, page: number) => {
      const qs = new URLSearchParams();
      if (tab !== 'summary') qs.set('tab', tab);
      if (page > 1) qs.set('page', String(page));
      const str = qs.toString();
      window.history.replaceState(window.history.state, '', str ? `${pathname}?${str}` : pathname);
    },
    [pathname]
  );

  const previousVnsRef = useRef<PovVN[]>([]);
  const prefetchCacheRef = useRef<Map<string, { vns: PovVN[]; page: number; pages: number; total: number }>>(new Map());
  const resultsRef = useRef<HTMLDivElement>(null);
  const hasAttemptedVnsRef = useRef(false);
  const isInitialBackNavRef = useRef(false);

  useEffect(() => {
    const isBackNav = sessionStorage.getItem('is-popstate-navigation') === 'true';
    sessionStorage.removeItem('is-popstate-navigation');
    if (isBackNav) {
      isInitialBackNavRef.current = true;
    }
  }, []);

  const loadNovels = useCallback(
    async (
      page: number = 1,
      rank: string = rankFilter,
      olang: LanguageFilterValue = languageFilter,
      sort: string = sortBy
    ) => {
      hasAttemptedVnsRef.current = true;
      const cacheKey = `${page}-${rank}-${olang}-${sort}`;

      if (prefetchCacheRef.current.has(cacheKey)) {
        const cached = prefetchCacheRef.current.get(cacheKey)!;
        prefetchCacheRef.current.delete(cacheKey);
        setVns(cached.vns);
        previousVnsRef.current = cached.vns;
        setCurrentPage(cached.page);
        setTotalPages(cached.pages);
        setTotalVns(cached.total);
        if (isInitialBackNavRef.current) {
          isInitialBackNavRef.current = false;
          consumePendingScroll();
        }
        return;
      }

      setIsLoadingTab(true);
      try {
        const response = await vndbStatsApi.getPovVNs(
          povId,
          page,
          24,
          sort,
          rank || undefined,
          olang === 'all' ? undefined : olang
        );
        if (response) {
          setVns(response.vns);
          previousVnsRef.current = response.vns;
          setCurrentPage(response.page);
          setTotalPages(response.pages);
          setTotalVns(response.total);

          if (response.page < response.pages) {
            const nextKey = `${response.page + 1}-${rank}-${olang}-${sort}`;
            if (!prefetchCacheRef.current.has(nextKey)) {
              vndbStatsApi
                .getPovVNs(
                  povId,
                  response.page + 1,
                  24,
                  sort,
                  rank || undefined,
                  olang === 'all' ? undefined : olang
                )
                .then((res) => {
                  if (res) {
                    prefetchCacheRef.current.set(nextKey, res);
                    preloadVNImages(res.vns);
                  }
                })
                .catch(() => {});
            }
          }
        }
      } catch {
        // Optional section
      } finally {
        setIsLoadingTab(false);
        if (isInitialBackNavRef.current) {
          isInitialBackNavRef.current = false;
          consumePendingScroll();
        }
      }
    },
    [povId, rankFilter, languageFilter, sortBy]
  );

  const handleTabChange = useCallback(
    (newTab: PovTabId) => {
      setActiveTab(newTab);
      setCurrentPage(1);
      setVns([]);
      hasAttemptedVnsRef.current = false;
      prefetchCacheRef.current.clear();
      updateUrl(newTab, 1);
    },
    [updateUrl]
  );

  const handlePageChange = useCallback(
    (newPage: number) => {
      loadNovels(newPage, rankFilter, languageFilter, sortBy);
      updateUrl(activeTab, newPage);
    },
    [loadNovels, rankFilter, languageFilter, sortBy, activeTab, updateUrl]
  );

  const handlePrefetchPage = useCallback(
    (page: number) => {
      const cacheKey = `${page}-${rankFilter}-${languageFilter}-${sortBy}`;
      if (prefetchCacheRef.current.has(cacheKey)) {
        preloadVNImages(prefetchCacheRef.current.get(cacheKey)!.vns);
        return;
      }
      vndbStatsApi
        .getPovVNs(
          povId,
          page,
          24,
          sortBy,
          rankFilter || undefined,
          languageFilter === 'all' ? undefined : languageFilter
        )
        .then((res) => {
          if (res) {
            prefetchCacheRef.current.set(cacheKey, res);
            preloadVNImages(res.vns);
          }
        })
        .catch(() => {});
    },
    [povId, rankFilter, languageFilter, sortBy]
  );

  useEffect(() => {
    const urlTab = tabFromUrl && VALID_TABS.includes(tabFromUrl) ? tabFromUrl : 'summary';
    const urlPage = pageFromUrl ? Math.max(1, parseInt(pageFromUrl, 10) || 1) : 1;
    if (urlTab !== activeTab) {
      setActiveTab(urlTab);
      setCurrentPage(urlPage);
      setVns([]);
      hasAttemptedVnsRef.current = false;
      prefetchCacheRef.current.clear();
    } else if (urlPage !== currentPage && urlTab === 'novels' && vns.length > 0) {
      setCurrentPage(urlPage);
      loadNovels(urlPage);
    }
  }, [tabFromUrl, pageFromUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadInitialData = useCallback(
    async (forceRefresh = false) => {
      setIsLoading(true);
      setError(null);
      try {
        const [statsData, simData, tagsData] = await Promise.all([
          vndbStatsApi.getPovStats(povId, { nocache: forceRefresh }),
          vndbStatsApi.getSimilarPovs(povId, 30),
          vndbStatsApi.getPovRelatedTags(povId, 30),
        ]);
        if (!statsData) {
          setError('POV not found.');
          return;
        }
        setStats(statsData);
        setSimilarPovs(simData);
        setRelatedTags(tagsData);
      } catch {
        setError('Failed to load POV data.');
      } finally {
        setIsLoading(false);
      }
    },
    [povId]
  );

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  useEffect(() => {
    if (activeTab === 'novels' && vns.length === 0 && !isLoadingTab && !hasAttemptedVnsRef.current) {
      loadNovels(currentPage);
    }
  }, [activeTab, vns.length, isLoadingTab, currentPage, loadNovels]);

  const handleRefresh = async () => {
    const now = Date.now();
    if (now < refreshBlockedUntil || isRefreshing) return;
    setIsRefreshing(true);
    setRefreshBlockedUntil(now + 8000);
    await loadInitialData(true);
    setIsRefreshing(false);
  };

  const handleRankChange = useCallback(
    (value: string) => {
      prefetchCacheRef.current.clear();
      setRankFilter(value);
      setCurrentPage(1);
      loadNovels(1, value, languageFilter, sortBy);
      updateUrl(activeTab, 1);
    },
    [activeTab, languageFilter, sortBy, loadNovels, updateUrl]
  );

  const handleSortChange = useCallback(
    (value: string) => {
      prefetchCacheRef.current.clear();
      setSortBy(value);
      setCurrentPage(1);
      loadNovels(1, rankFilter, languageFilter, value);
      updateUrl(activeTab, 1);
    },
    [activeTab, rankFilter, languageFilter, loadNovels, updateUrl]
  );

  const handleLanguageChange = useCallback(
    (value: LanguageFilterValue) => {
      prefetchCacheRef.current.clear();
      setLanguageFilter(value);
      setCurrentPage(1);
      loadNovels(1, rankFilter, value, sortBy);
      updateUrl(activeTab, 1);
    },
    [activeTab, rankFilter, sortBy, loadNovels, updateUrl]
  );

  if (isLoading) {
    return <LoadingScreen title="Loading POV stats..." subtitle="Crunching ErogameScape & VNDB data for this POV" />;
  }

  if (error || !stats) {
    return <ErrorState error={error} povId={povId} />;
  }

  const pov = stats.pov;
  const numericPovId = pov.id;
  const totalRankVotes = Math.max(1, pov.rank_a + pov.rank_b + pov.rank_c);

  return (
    <div className="relative max-w-7xl mx-auto px-4 py-8 overflow-x-clip">
      {isRefreshing && (
        <div className="absolute inset-0 z-30 flex items-center justify-center bg-[color:var(--surface)] backdrop-blur-xs">
          <div className="flex items-center gap-2 px-4 py-2 rounded-xs bg-[color:var(--surface)] border border-[color:var(--rule)]">
            <RefreshCw className="w-4 h-4 animate-spin text-[color:var(--ai)]" />
            <span className="text-sm text-[color:var(--text-secondary)]">Refreshing…</span>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 mb-8">
        <div className="flex items-start gap-4">
          <button
            onClick={() => window.history.back()}
            aria-label="Go back"
            className="st-act st-act--icon mt-1"
          >
            <ArrowLeft className="w-5 h-5 text-[color:var(--nezu)]" />
          </button>
          <div>
            {/* Breadcrumb */}
            <div className="flex items-center gap-1 text-sm text-[color:var(--nezu)] mb-2 flex-wrap">
              <Link href="/stats/pov/" className="hover:text-[color:var(--ai)]">
                EGS POVs
              </Link>
              {pov.system_group && (
                <>
                  <ChevronRight className="w-3 h-3 shrink-0" />
                  <Link
                    href={`/stats/pov/?group=${encodeURIComponent(pov.system_group)}`}
                    className="hover:text-[color:var(--ai)]"
                  >
                    {pov.system_group}
                  </Link>
                </>
              )}
              <ChevronRight className="w-3 h-3 shrink-0" />
              <span className="text-[color:var(--text-secondary)]">{pov.title}</span>
            </div>

            <div className="flex flex-wrap items-center gap-2 mb-1">
              <h1 className="sec-title">{pov.title}</h1>
              {pov.system_title && pov.system_title !== pov.title && (
                <span className="st-badge">{pov.system_title}</span>
              )}
              {pov.system_group && (
                <Link
                  href={`/stats/pov/?group=${encodeURIComponent(pov.system_group)}`}
                  className="st-badge hover:border-[color:var(--ai)]"
                >
                  {pov.system_group}
                </Link>
              )}
              {pov.spoiler && (
                <span className="st-badge text-[color:var(--aka)]">Spoiler</span>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <a
                href={pov.url}
                target="_blank"
                rel="noopener noreferrer"
                className="sec-more"
              >
                View on ErogameScape <span aria-hidden>↗</span>
              </a>
              <Link href="/stats/pov/" className="sec-more">
                All POVs <span aria-hidden>→</span>
              </Link>
            </div>

            {pov.comment && (
              <div className="mt-3 text-sm text-[color:var(--nezu)] max-w-2xl wrap-break-word whitespace-pre-line">
                <p>
                  {!showFullComment && pov.comment.length > 300
                    ? pov.comment.substring(0, 300) + '...'
                    : pov.comment}
                </p>
                {pov.comment.length > 300 && (
                  <button
                    onClick={() => setShowFullComment(!showFullComment)}
                    className="sec-more mt-1"
                  >
                    {showFullComment ? 'Show less' : 'Show more'}
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="flex flex-col items-end gap-2">
          <button
            onClick={handleRefresh}
            disabled={isRefreshing || Date.now() < refreshBlockedUntil}
            className="st-act disabled:cursor-not-allowed"
          >
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
            {isRefreshing ? 'Refreshing…' : 'Refresh data'}
          </button>
          <LastUpdated timestamp={stats.last_updated} />
        </div>
      </div>

      {/* Tabs */}
      <PovDetailTabs
        activeTab={activeTab}
        onTabChange={handleTabChange}
        counts={{
          novels: pov.vn_count,
          similarPovs: similarPovs.length,
          relatedTags: relatedTags.length,
        }}
      />

      {/* Summary Tab */}
      {activeTab === 'summary' && (
        <>
          {/* Sibling POVs in same system group */}
          {stats.group_povs.length > 0 && pov.system_group && (
            <div className="st-card p-4 mb-8">
              <h2 className="fig-label mb-3">
                More in {pov.system_group} ({stats.group_povs.length})
              </h2>
              <div className="flex flex-wrap gap-2">
                {stats.group_povs.map((sib) => (
                  <Link
                    key={sib.id}
                    href={`/stats/pov/${sib.id}`}
                    className="st-chip"
                  >
                    {sib.system_title || sib.title}
                    {sib.spoiler && <span className="text-[color:var(--aka)] ml-1">!</span>}
                    <span className="text-[color:var(--text-faint)] ml-1.5">
                      ({sib.vn_count.toLocaleString()})
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <StatsSummaryCard
              label="VNDB Avg Rating"
              value={stats.average_rating > 0 ? stats.average_rating.toFixed(2) : 'N/A'}
              subtext={`from ${stats.total_vns.toLocaleString()} rated VNs`}
            />
            <StatsSummaryCard
              label="EGS Avg Median"
              value={stats.egs_average_median != null ? stats.egs_average_median.toFixed(1) : 'N/A'}
              subtext={`from ${stats.egs_rated_games.toLocaleString()} EGS titles`}
            />
            <StatsSummaryCard
              label="EGS POV Votes"
              value={pov.total_votes.toLocaleString()}
              subtext={`${pov.user_count.toLocaleString()} EGS users`}
            />
            <StatsSummaryCard
              label="Mapped VNs"
              value={pov.vn_count.toLocaleString()}
              subtext={`of ${pov.egs_game_count.toLocaleString()} EGS titles`}
            />
          </div>

          {/* EGS Rank Breakdown (A / B / C) */}
          {(pov.rank_a + pov.rank_b + pov.rank_c) > 0 && (
            <div className="st-card p-5 mb-8">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                <h2 className="st-card-title">ErogameScape Match Strength (A / B / C)</h2>
                <span className="fig-label st-num">
                  {(pov.rank_a + pov.rank_b + pov.rank_c).toLocaleString()} total POV votes
                </span>
              </div>
              <div className="h-3 w-full rounded-xs overflow-hidden flex bg-[color:var(--surface-inset)] mb-3">
                <div
                  className="bg-[color:var(--ai)] h-full"
                  style={{ width: `${(pov.rank_a / totalRankVotes) * 100}%` }}
                  title={`Rank A: ${pov.rank_a.toLocaleString()}`}
                />
                <div
                  className="bg-[color:var(--kiku)] h-full"
                  style={{ width: `${(pov.rank_b / totalRankVotes) * 100}%` }}
                  title={`Rank B: ${pov.rank_b.toLocaleString()}`}
                />
                <div
                  className="bg-[color:var(--nezu)] h-full opacity-60"
                  style={{ width: `${(pov.rank_c / totalRankVotes) * 100}%` }}
                  title={`Rank C: ${pov.rank_c.toLocaleString()}`}
                />
              </div>
              <div className="grid grid-cols-3 gap-3 text-xs">
                <div>
                  <span className="font-mono font-bold text-[color:var(--ai)]">Rank A</span>
                  <span className="text-[color:var(--nezu)] ml-1.5">(Strong)</span>
                  <div className="st-num text-sm text-[color:var(--ink)] mt-0.5">
                    {pov.rank_a.toLocaleString()}{' '}
                    <span className="text-[color:var(--text-faint)]">
                      ({Math.round((pov.rank_a / totalRankVotes) * 100)}%)
                    </span>
                  </div>
                </div>
                <div>
                  <span className="font-mono font-bold text-[color:var(--kiku)]">Rank B</span>
                  <span className="text-[color:var(--nezu)] ml-1.5">(Moderate)</span>
                  <div className="st-num text-sm text-[color:var(--ink)] mt-0.5">
                    {pov.rank_b.toLocaleString()}{' '}
                    <span className="text-[color:var(--text-faint)]">
                      ({Math.round((pov.rank_b / totalRankVotes) * 100)}%)
                    </span>
                  </div>
                </div>
                <div>
                  <span className="font-mono font-bold text-[color:var(--nezu)]">Rank C</span>
                  <span className="text-[color:var(--nezu)] ml-1.5">(Mild)</span>
                  <div className="st-num text-sm text-[color:var(--ink)] mt-0.5">
                    {pov.rank_c.toLocaleString()}{' '}
                    <span className="text-[color:var(--text-faint)]">
                      ({Math.round((pov.rank_c / totalRankVotes) * 100)}%)
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <ScoreDistributionChart
              headingLevel="h2"
              distribution={stats.score_distribution}
              jpDistribution={stats.score_distribution_jp}
              average={stats.average_rating}
              entityId={String(numericPovId)}
              entityType="pov"
              entityName={pov.title}
            />
            <EgsMedianDistributionCard
              distribution={stats.egs_median_distribution}
              average={stats.egs_average_median}
              totalRated={stats.egs_rated_games}
            />
            <ReleaseYearChart
              headingLevel="h2"
              distribution={stats.release_year_distribution}
              distributionWithRatings={stats.release_year_with_ratings}
              entityId={String(numericPovId)}
              entityType="pov"
              entityName={pov.title}
            />
            <LengthChart
              headingLevel="h2"
              distribution={stats.length_distribution}
              entityId={String(numericPovId)}
              entityType="pov"
              entityName={pov.title}
            />
            {Object.keys(stats.age_rating_distribution).length > 0 && (
              <AgeRatingChart
                headingLevel="h2"
                distribution={stats.age_rating_distribution}
                entityId={String(numericPovId)}
                entityType="pov"
                entityName={pov.title}
              />
            )}
          </div>
        </>
      )}

      {/* Novels Tab */}
      {activeTab === 'novels' && (
        <div ref={resultsRef} className="st-card p-6">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
            <p className="text-sm text-[color:var(--nezu)]">
              {totalVns > 0 && `${totalVns.toLocaleString()} mapped visual novels`}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <SimpleSelect
                options={[
                  { value: '', label: 'All Ranks' },
                  { value: 'A', label: 'Rank A' },
                  { value: 'B', label: 'Rank B' },
                  { value: 'C', label: 'Rank C' },
                ]}
                value={rankFilter}
                onChange={handleRankChange}
                compact
              />
              <SimpleSelect
                options={[
                  { value: 'pov_votes', label: 'POV Votes' },
                  { value: 'rating', label: 'VNDB Rating' },
                  { value: 'egs_median', label: 'EGS Median' },
                  { value: 'votecount', label: 'VNDB Votes' },
                  { value: 'released', label: 'Release Date' },
                ]}
                value={sortBy}
                onChange={handleSortChange}
                compact
              />
              <LanguageFilter value={languageFilter} onChange={handleLanguageChange} />
            </div>
          </div>

          {(isLoadingTab || !hasAttemptedVnsRef.current) && vns.length === 0 && previousVnsRef.current.length === 0 ? (
            <>
              <PaginationSkeleton />
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {Array.from({ length: 12 }).map((_, i) => (
                  <VNCardSkeleton key={i} />
                ))}
              </div>
              <PaginationSkeleton />
            </>
          ) : (
            <div>
              {vns.length > 0 || previousVnsRef.current.length > 0 ? (
                <div className={isLoadingTab ? 'pointer-events-none opacity-75' : ''}>
                  {totalPages > 1 && (
                    <Pagination
                      currentPage={currentPage}
                      totalPages={totalPages}
                      onPageChange={handlePageChange}
                      onPrefetchPage={handlePrefetchPage}
                      totalItems={totalVns}
                      itemsPerPage={24}
                    />
                  )}
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {(vns.length > 0 ? vns : previousVnsRef.current).map((vn) => (
                      <PovVNCard key={vn.id} vn={vn} />
                    ))}
                  </div>
                  {totalPages > 1 && (
                    <Pagination
                      currentPage={currentPage}
                      totalPages={totalPages}
                      onPageChange={handlePageChange}
                      onPrefetchPage={handlePrefetchPage}
                      totalItems={totalVns}
                      itemsPerPage={24}
                      scrollTargetRef={resultsRef}
                    />
                  )}
                </div>
              ) : (
                hasAttemptedVnsRef.current && (
                  <p className="st-card-sub py-8 text-center">
                    No visual novels found with this POV{languageFilter === 'ja' ? ' (Japanese only)' : ''}.
                  </p>
                )
              )}
            </div>
          )}
        </div>
      )}

      {/* Similar POVs Tab */}
      {activeTab === 'similar-povs' && (
        <div className="st-card p-6">
          {similarPovs.length > 0 ? (
            <div className="space-y-3">
              {similarPovs.map((simPov) => (
                <SimilarPovRow key={simPov.id} pov={simPov} />
              ))}
            </div>
          ) : (
            <p className="st-card-sub py-8 text-center">No similar POVs found.</p>
          )}
        </div>
      )}

      {/* Related VNDB Tags Tab */}
      {activeTab === 'related-tags' && (
        <div className="st-card p-6">
          {relatedTags.length > 0 ? (
            <div className="space-y-3">
              {relatedTags.map((tag) => (
                <RelatedTagRow key={tag.id} tag={tag} />
              ))}
            </div>
          ) : (
            <p className="st-card-sub py-8 text-center">No related VNDB tags found.</p>
          )}
        </div>
      )}
    </div>
  );
}

function EgsMedianDistributionCard({
  distribution,
  average,
  totalRated,
}: {
  distribution: Record<string, number>;
  average?: number | null;
  totalRated: number;
}) {
  const buckets = useMemo(() => {
    return Array.from({ length: 10 }, (_, i) => {
      const start = i * 10;
      const end = start === 90 ? 100 : start + 9;
      return {
        key: String(start),
        label: `${start}`,
        rangeLabel: `${start}–${end}`,
        count: distribution[String(start)] || 0,
      };
    });
  }, [distribution]);

  const maxCount = Math.max(...buckets.map((b) => b.count), 1);

  if (totalRated === 0) {
    return (
      <div className="st-card p-6">
        <h2 className="st-card-title mb-4">ErogameScape Median Distribution</h2>
        <p className="st-card-sub">No ErogameScape rating data available</p>
      </div>
    );
  }

  return (
    <div className="st-card p-6">
      <div className="st-card-head mb-4">
        <h2 className="st-card-title">ErogameScape Median Distribution</h2>
        {average != null && (
          <span className="fig-label">
            Avg <span className="st-num text-[color:var(--ink)]">{average.toFixed(1)}</span>/100
          </span>
        )}
      </div>

      <div className="flex items-end gap-2 h-48">
        {buckets.map((b) => {
          const heightPx = (b.count / maxCount) * 160;
          const pct = totalRated > 0 ? ((b.count / totalRated) * 100).toFixed(0) : '0';
          const isAvgBucket =
            average != null && b.count > 0 && Math.min(90, Math.floor(average / 10) * 10) === Number(b.key);
          return (
            <div key={b.key} className="group relative flex-1 flex flex-col items-center justify-end h-full">
              <div className="st-num text-center text-xs leading-tight text-[color:var(--nezu)] mb-1">
                {b.count > 0 ? b.count : ''}
              </div>
              <div
                className={`relative w-full ${isAvgBucket ? 'st-col st-col--on' : 'st-col'}`}
                style={{ height: `${Math.max(heightPx, b.count > 0 ? 4 : 0)}px` }}
              >
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 st-tip on-box opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap pointer-events-none z-10">
                  <span>{b.rangeLabel} pts: {b.count} titles ({pct}%)</span>
                </div>
              </div>
              <div className={`st-num text-xs mt-1.5 ${isAvgBucket ? 'text-[color:var(--ai)] font-semibold' : 'text-[color:var(--nezu)]'}`}>
                {b.label}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 border-t border-[color:var(--rule)] pt-4">
        <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 font-mono text-xs tabular-nums text-[color:var(--nezu)]">
          <span>EGS rated titles (10+ votes): {totalRated.toLocaleString()}</span>
          <span>
            Peak bucket: {buckets.reduce((max, b) => (b.count > max.count ? b : max), buckets[0]).rangeLabel}
          </span>
        </div>
      </div>
    </div>
  );
}

function PovVNCard({ vn }: { vn: PovVN }) {
  const { preference } = useTitlePreference();
  const { loaded, error, retryKey, onLoad, onError } = useImageRetry();
  const displayTitle = getDisplayTitle(
    { title: vn.title, title_jp: vn.title_jp || undefined, title_romaji: vn.title_romaji || undefined },
    preference
  );
  const showImage = vn.image_url && !error;

  return (
    <Link href={`/vn/${vn.id}`} className="st-card st-card--pick flex gap-3 p-3">
      <div className="w-16 h-20 shrink-0 relative overflow-hidden rounded-xs">
        {showImage && !loaded && <div className="absolute inset-0 image-placeholder" />}
        {showImage ? (
          <NSFWImage
            src={addRetryKey(getProxiedImageUrl(vn.image_url!, { width: 128, vnId: vn.id }) || '', retryKey)}
            alt={displayTitle}
            vnId={vn.id}
            imageSexual={vn.image_sexual}
            className={`w-full h-full object-cover ${loaded ? 'opacity-100' : 'opacity-0'}`}
            onLoad={onLoad}
            onError={onError}
            compact
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-[color:var(--surface-inset)]" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <h4 className="dg-name block">{displayTitle}</h4>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[color:var(--nezu)] mt-0.5">
          {vn.released && <span>{vn.released.substring(0, 4)}</span>}
          <span
            className="font-mono font-bold text-[color:var(--ai)]"
            title={`A: ${vn.pov_rank_a} · B: ${vn.pov_rank_b} · C: ${vn.pov_rank_c}`}
          >
            Rank {vn.pov_rank}
          </span>
          <span className="st-num text-[color:var(--text-faint)]">
            {vn.pov_votes.toLocaleString()} POV votes
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2.5 mt-1 text-xs">
          {vn.rating != null && (
            <span className="font-medium text-[color:var(--text-secondary)]">
              VNDB {vn.rating.toFixed(2)}
              {vn.votecount > 0 && (
                <span className="text-[color:var(--nezu)] font-normal ml-0.5">
                  ({vn.votecount.toLocaleString()})
                </span>
              )}
            </span>
          )}
          {vn.egs_median != null && (
            <span className="font-mono text-[color:var(--nezu)]">
              EGS {vn.egs_median}
            </span>
          )}
        </div>
        {vn.tags && vn.tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {sortTagsByWeight(vn.tags).slice(0, 3).map((t) => (
              <span key={t.id} className="st-badge">
                {t.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </Link>
  );
}

function SimilarPovRow({ pov }: { pov: SimilarPov }) {
  const percentage = Math.round(pov.similarity * 100);
  return (
    <Link
      href={`/stats/pov/${pov.id}`}
      className="st-row flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
    >
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <span className="dg-name">{pov.title}</span>
        {pov.system_group && <span className="st-badge shrink-0">{pov.system_group}</span>}
        {pov.spoiler && <span className="st-badge text-[color:var(--aka)] shrink-0">Spoiler</span>}
      </div>
      <div className="flex items-center justify-between sm:justify-end gap-4">
        <span className="st-num text-xs text-[color:var(--text-faint)] whitespace-nowrap">
          {pov.shared_vn_count.toLocaleString()} shared VNs ({pov.shared_game_count.toLocaleString()} EGS)
        </span>
        <div className="w-24 flex items-center gap-2">
          <div className="st-bar h-2 flex-1">
            <div className="st-bar-fill" style={{ width: `${percentage}%` }} />
          </div>
          <span className="st-num w-10 text-right text-xs text-[color:var(--ink)]">{percentage}%</span>
        </div>
      </div>
    </Link>
  );
}

function RelatedTagRow({ tag }: { tag: PovRelatedTag }) {
  const percentage = Math.round(tag.frequency * 100);
  return (
    <Link
      href={`/stats/tag/${tag.id}`}
      className="st-row flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
    >
      <div className="flex items-center gap-2 min-w-0">
        <span className="dg-name">{tag.name}</span>
        {tag.category && (
          <span className="st-badge shrink-0">{TAG_CATEGORY_LABELS[tag.category] || tag.category}</span>
        )}
      </div>
      <div className="flex items-center justify-between sm:justify-end gap-4">
        <span className="text-sm text-[color:var(--nezu)] whitespace-nowrap">
          {tag.vn_count.toLocaleString()} VNs
        </span>
        <div className="w-24 flex items-center gap-2">
          <div className="st-bar h-2 flex-1">
            <div className="st-bar-fill" style={{ width: `${Math.min(100, percentage)}%` }} />
          </div>
          <span className="st-num w-10 text-right text-xs text-[color:var(--ink)]">{percentage}%</span>
        </div>
      </div>
    </Link>
  );
}

function ErrorState({ error, povId }: { error: string | null; povId: string }) {
  const numericId = povId.replace(/\D/g, '');
  return (
    <div className="max-w-2xl mx-auto px-4 py-16 text-center">
      <h1 className="sec-title mb-2">Unable to Load POV</h1>
      <p className="text-[color:var(--nezu)] mb-6">
        {error || 'Something went wrong while loading the ErogameScape POV data.'}
      </p>
      <div className="flex flex-col sm:flex-row gap-3 justify-center">
        <button onClick={() => window.history.back()} className="st-act st-act--go">
          <ArrowLeft className="w-4 h-4" />
          Go Back
        </button>
        {numericId && (
          <a
            href={`https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/pov.php?pov_id=${numericId}`}
            target="_blank"
            rel="noopener noreferrer"
            className="st-act"
          >
            Check on ErogameScape
            <ExternalLink className="w-4 h-4" />
          </a>
        )}
      </div>
    </div>
  );
}

function VNCardSkeleton() {
  return (
    <div className="st-card flex gap-3 p-3">
      <div className="w-16 h-20 shrink-0 rounded-xs image-placeholder" />
      <div className="flex-1 min-w-0 space-y-2">
        <div className="h-4 w-4/5 rounded-xs image-placeholder" />
        <div className="h-3 w-12 rounded-xs image-placeholder" />
        <div className="h-3 w-20 rounded-xs image-placeholder" />
        <div className="flex gap-1 mt-2">
          <div className="h-4 w-14 rounded-xs image-placeholder" />
          <div className="h-4 w-12 rounded-xs image-placeholder" />
        </div>
      </div>
    </div>
  );
}
