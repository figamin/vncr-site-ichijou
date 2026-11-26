import { Metadata } from 'next';
import Link from '@/components/Link';
import { ChevronRight } from 'lucide-react';
import { generatePageMetadata } from '@/lib/metadata-utils';
import { BrowsePovsTab } from '@/components/browse/BrowsePovsTab';
import { VNDBAttribution } from '@/components/VNDBAttribution';

export const metadata: Metadata = generatePageMetadata({
  title: 'ErogameScape POVs',
  description:
    'Browse ErogameScape community POV categories across mapped VNDB visual novels — ratings, vote counts, match ranks, and distributions.',
  path: '/stats/pov/',
});

export default function PovIndexPage() {
  return (
    <div className="min-h-screen bg-[color:var(--ground)]">
      <div className="max-w-[1400px] mx-auto px-4 pt-6 pb-10">
        <div className="flex items-center gap-1 text-sm text-[color:var(--nezu)] mb-2 flex-wrap">
          <Link href="/" className="hover:text-[color:var(--ai)]">Home</Link>
          <ChevronRight className="w-3 h-3 shrink-0" />
          <Link href="/stats/" className="hover:text-[color:var(--ai)]">Stats</Link>
          <ChevronRight className="w-3 h-3 shrink-0" />
          <span className="text-[color:var(--text-secondary)]">EGS POVs</span>
        </div>

        <div className="mb-6">
          <h1 className="sec-title">ErogameScape POVs</h1>
          <p className="sec-sub">
            Community-voted visual novel traits &amp; points of view from ErogameScape, cross-referenced with VNDB.
          </p>
        </div>

        <BrowsePovsTab isActive basePath="/stats/pov/" />

        <div className="mt-6">
          <VNDBAttribution />
        </div>
      </div>
    </div>
  );
}
