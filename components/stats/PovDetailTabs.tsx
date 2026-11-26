'use client';

export type PovTabId = 'summary' | 'novels' | 'similar-povs' | 'related-tags';

interface Tab {
  id: PovTabId;
  label: string;
  count?: number;
}

interface PovDetailTabsProps {
  activeTab: PovTabId;
  onTabChange: (tab: PovTabId) => void;
  counts?: {
    novels?: number;
    similarPovs?: number;
    relatedTags?: number;
  };
}

export function PovDetailTabs({ activeTab, onTabChange, counts }: PovDetailTabsProps) {
  const tabs: Tab[] = [
    {
      id: 'summary',
      label: 'Summary',
    },
    {
      id: 'novels',
      label: 'Novels',
      count: counts?.novels,
    },
    {
      id: 'similar-povs',
      label: 'Similar POVs',
      count: counts?.similarPovs,
    },
    {
      id: 'related-tags',
      label: 'Related Tags',
      count: counts?.relatedTags,
    },
  ];

  return (
    <div className="mb-6">
      <nav className="tabs sm:flex-nowrap sm:overflow-x-auto" aria-label="Tabs">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`tab ${isActive ? 'tab--on' : ''}`}
            >
              {tab.label}
              {tab.count !== undefined && (
                <span className="tab-count">{tab.count.toLocaleString()}</span>
              )}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
