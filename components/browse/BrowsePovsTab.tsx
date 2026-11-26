'use client';

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { Search, X } from 'lucide-react';
import { mutate } from 'swr';
import { vndbStatsApi, BrowsePovItem, BrowsePovParams } from '@/lib/vndb-stats-api';
import { useBrowsePovs } from '@/lib/vndb-stats-cached';
import { Pagination, PaginationSkeleton } from './Pagination';
import { EntityTable, EntityColumn, BadgeCell, CountCell } from './EntityTable';
import { EntityCards, EntityCard } from './EntityCards';
import { SimpleSelect } from './SimpleSelect';
import { EntityViewToggle, ViewMode } from './EntityViewToggle';
import { RandomButton } from './RandomButton';

const ITEMS_PER_PAGE = 50;
const FILTER_DEBOUNCE_MS = 150;
const SEARCH_DEBOUNCE_MS = 300;

interface BrowsePovsTabProps {
  isActive?: boolean;
  /** Route path used for pagination hrefs (default '/browse/'). */
  basePath?: string;
}

function initialGroupFromUrl(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  const g = new URLSearchParams(window.location.search).get('group');
  return g || undefined;
}

function initialPage(basePath: string): number {
  if (typeof window === 'undefined') return 1;
  const search = new URLSearchParams(window.location.search);
  if (basePath.startsWith('/browse') && search.get('tab') !== 'povs') return 1;
  const page = Number(search.get('page'));
  return Number.isInteger(page) && page > 1 ? page : 1;
}

export function BrowsePovsTab({ isActive = true, basePath = '/browse/' }: BrowsePovsTabProps) {
  const [params, setParams] = useState<BrowsePovParams>({
    group: initialGroupFromUrl(),
    include_spoilers: false,
    sort: 'vn_count',
    sort_order: 'desc',
    page: initialPage(basePath),
    limit: ITEMS_PER_PAGE,
  });
  const [searchInput, setSearchInput] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('table');
  const [debouncedParams, setDebouncedParams] = useState(params);

  const filterDebounceRef = useRef<NodeJS.Timeout | null>(null);
  const searchDebounceRef = useRef<NodeJS.Timeout | null>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  const { data, error, isLoading, isValidating } = useBrowsePovs(debouncedParams, isActive);

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const page = data?.page ?? 1;
  const pages = data?.pages ?? 1;
  const groupOptions = useMemo(
    () => [
      { value: '', label: 'All Groups' },
      ...(data?.groups ?? []).map((g) => ({ value: g.group, label: `${g.group} (${g.count})` })),
    ],
    [data?.groups]
  );

  useEffect(() => {
    if (window.innerWidth < 768) setViewMode('cards');
  }, []);

  useEffect(() => {
    return () => {
      if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current);
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, []);

  const updateParams = (updates: Partial<BrowsePovParams>) => {
    const updated = { ...params, ...updates, page: updates.page ?? 1 };
    setParams(updated);

    if (updates.page !== undefined && Object.keys(updates).length === 1) {
      setDebouncedParams(updated);
    } else {
      if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current);
      filterDebounceRef.current = setTimeout(() => {
        setDebouncedParams(updated);
      }, FILTER_DEBOUNCE_MS);
    }
  };

  const handleSearchChange = (value: string) => {
    setSearchInput(value);
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      updateParams({ q: value || undefined });
    }, SEARCH_DEBOUNCE_MS);
  };

  const getPageHref = useCallback(
    (targetPage: number) => {
      if (typeof window === 'undefined') return '#';
      const qs = new URLSearchParams(window.location.search);
      if (basePath.startsWith('/browse')) qs.set('tab', 'povs');
      if (targetPage > 1) qs.set('page', String(targetPage));
      else qs.delete('page');
      const s = qs.toString();
      return s ? `${basePath}?${s}` : basePath;
    },
    [basePath]
  );

  const handlePrefetchPage = useCallback(
    (targetPage: number) => {
      if (targetPage < 1 || targetPage > pages) return;
      const prefetchParams = { ...debouncedParams, page: targetPage };
      const key = ['browsePovs', JSON.stringify(prefetchParams)];
      mutate(key, vndbStatsApi.browsePovs(prefetchParams), { revalidate: false });
    },
    [debouncedParams, pages]
  );

  useEffect(() => {
    if (!data || isLoading) return;
    const timer = setTimeout(() => {
      if (page < pages) handlePrefetchPage(page + 1);
    }, 200);
    return () => clearTimeout(timer);
  }, [data, page, pages, isLoading, handlePrefetchPage]);

  const columns: EntityColumn<BrowsePovItem>[] = [
    {
      key: 'title',
      label: 'POV',
      render: (item) => (
        <div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[color:var(--ink)]">{item.title}</span>
            {item.spoiler && <span className="bw-badge text-[color:var(--aka)]">Spoiler</span>}
          </div>
          {item.system_title && item.system_title !== item.title && (
            <div className="text-xs text-[color:var(--text-faint)] mt-0.5">{item.system_title}</div>
          )}
        </div>
      ),
    },
    {
      key: 'system_group',
      label: 'Group',
      render: (item) =>
        item.system_group ? (
          <BadgeCell value={item.system_group} />
        ) : (
          <span className="text-[color:var(--text-faint)]">—</span>
        ),
    },
    {
      key: 'vn_count',
      label: 'VNs',
      className: 'text-right',
      render: (item) => <CountCell count={item.vn_count} />,
    },
    {
      key: 'total_votes',
      label: 'POV Votes',
      className: 'text-right',
      render: (item) => <CountCell count={item.total_votes} />,
    },
    {
      key: 'avg_egs_median',
      label: 'EGS Median',
      className: 'text-right',
      render: (item) => (
        <span className="bw-num text-[color:var(--nezu)]">
          {item.avg_egs_median != null ? item.avg_egs_median.toFixed(1) : '—'}
        </span>
      ),
    },
    {
      key: 'comment',
      label: 'Description',
      className: 'max-w-md',
      render: (item) => (
        <span className="text-[color:var(--nezu)] text-xs line-clamp-2">
          {item.comment || '—'}
        </span>
      ),
    },
  ];

  const failed = Boolean(error) && items.length === 0;
  const showLoadingSkeleton = !data && !failed;
  const showLoadingIndicator = (!data && !failed) || isLoading || isValidating;

  return (
    <div className="space-y-4">
      {/* Search + Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[color:var(--text-faint)]" />
          <input
            type="search"
            autoComplete="off"
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder="Search ErogameScape POVs (Japanese, furigana, group, description)..."
            className="bw-field w-full pl-9 pr-8 py-2 text-sm"
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => handleSearchChange('')}
              className="bw-chip-btn absolute right-3 top-1/2 -translate-y-1/2"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        <SimpleSelect
          options={groupOptions}
          value={params.group || ''}
          onChange={(v) => updateParams({ group: v || undefined })}
        />

        <button
          type="button"
          onClick={() => updateParams({ include_spoilers: !params.include_spoilers })}
          aria-pressed={Boolean(params.include_spoilers)}
          className={`tab shrink-0${params.include_spoilers ? ' tab--on' : ''}`}
        >
          Include spoilers
        </button>
      </div>

      {/* Results Header */}
      <div ref={resultsRef} className="scroll-mt-20 flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm text-[color:var(--nezu)]">
          <span>
            <span className="bw-num text-[color:var(--ink)]">{total.toLocaleString()}</span> POVs
          </span>
        </span>
        <div className="flex items-center gap-2">
          <SimpleSelect
            options={[
              { value: 'vn_count', label: 'VN Count' },
              { value: 'total_votes', label: 'POV Votes' },
              { value: 'egs_game_count', label: 'EGS Titles' },
              { value: 'user_count', label: 'Users' },
              { value: 'avg_egs_median', label: 'EGS Median' },
              { value: 'title', label: 'Title' },
            ]}
            value={params.sort || 'vn_count'}
            onChange={(v) => updateParams({ sort: v as BrowsePovParams['sort'] })}
            compact
          />
          <button
            onClick={() => updateParams({ sort_order: params.sort_order === 'desc' ? 'asc' : 'desc' })}
            className="tab"
          >
            {params.sort_order === 'desc' ? '↓' : '↑'}
            <span className="hidden sm:inline text-xs ml-1">
              {params.sort_order === 'desc' ? 'Desc' : 'Asc'}
            </span>
          </button>
          <RandomButton entityType="povs" />
          <EntityViewToggle mode={viewMode} onChange={setViewMode} />
        </div>
      </div>

      {showLoadingSkeleton ? (
        <PaginationSkeleton />
      ) : pages > 1 ? (
        <Pagination
          currentPage={page}
          totalPages={pages}
          onPageChange={(p) => updateParams({ page: p })}
          onPrefetchPage={handlePrefetchPage}
          getPageHref={getPageHref}
          totalItems={total}
          itemsPerPage={ITEMS_PER_PAGE}
        />
      ) : null}

      <div key={showLoadingSkeleton ? 'loading' : 'loaded'} className={showLoadingSkeleton ? undefined : 'animate-fade-in'}>
        {viewMode === 'table' ? (
          <EntityTable
            items={items}
            columns={columns}
            getKey={(item) => item.id}
            getLink={(item) => `/stats/pov/${item.id}`}
            isLoading={showLoadingIndicator}
            failed={failed}
          />
        ) : (
          <EntityCards
            isLoading={showLoadingSkeleton}
            isValidating={isValidating && items.length > 0}
            isEmpty={items.length === 0 && !showLoadingIndicator && !failed}
            failed={failed}
          >
            {items.map((item) => (
              <EntityCard
                key={item.id}
                link={`/stats/pov/${item.id}`}
                title={item.title}
                fields={[
                  { label: 'Group', value: item.system_group || '—' },
                  { label: 'Mapped VNs', value: item.vn_count.toLocaleString() },
                  { label: 'POV Votes', value: item.total_votes.toLocaleString() },
                ]}
                rightContent={
                  <span className="bw-num text-lg text-[color:var(--ai)]">{item.vn_count.toLocaleString()}</span>
                }
                badges={
                  item.system_group ? (
                    <span className="bw-badge">{item.system_group}</span>
                  ) : undefined
                }
              />
            ))}
          </EntityCards>
        )}
      </div>

      {!showLoadingSkeleton && pages > 1 && (
        <Pagination
          currentPage={page}
          totalPages={pages}
          onPageChange={(p) => updateParams({ page: p })}
          onPrefetchPage={handlePrefetchPage}
          getPageHref={getPageHref}
          totalItems={total}
          itemsPerPage={ITEMS_PER_PAGE}
          scrollTargetRef={resultsRef}
        />
      )}
    </div>
  );
}
