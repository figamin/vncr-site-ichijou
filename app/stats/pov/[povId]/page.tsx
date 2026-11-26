import { Metadata } from 'next';
import { getPovForMetadata } from '@/lib/vndb-server';
import {
  generatePageMetadata,
  truncateDescription,
  safeJsonLdStringify,
  SITE_URL,
  generateBreadcrumbJsonLd,
} from '@/lib/metadata-utils';
import PovDetailClient from './PovDetailClient';

interface PageProps {
  params: Promise<{ povId: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { povId } = await params;
  const pov = await getPovForMetadata(povId);

  if (!pov) {
    return {
      title: `POV ${povId} Stats`,
      description: 'ErogameScape POV statistics and visual novel breakdown on VN Club.',
      robots: { index: false, follow: true },
    };
  }

  const label = pov.system_title && pov.system_title !== pov.title
    ? `${pov.title} (${pov.system_title})`
    : pov.title;

  const description = pov.comment
    ? truncateDescription(pov.comment, 200)
    : `${label}: ErogameScape POV statistics, score distributions, and tagged visual novels on VN Club.`;

  return generatePageMetadata({
    title: `${pov.title} - EGS POV Stats`,
    description,
    path: `/stats/pov/${povId}/`,
  });
}

export default async function PovDetailPage({ params }: PageProps) {
  const { povId } = await params;
  const pov = await getPovForMetadata(povId);

  const jsonLd = pov
    ? [
        {
          '@context': 'https://schema.org',
          '@type': 'Thing',
          name: pov.title,
          description: pov.comment ? truncateDescription(pov.comment, 500) : undefined,
          url: `${SITE_URL}/stats/pov/${povId}/`,
        },
        generateBreadcrumbJsonLd([
          { name: 'Home', path: '/' },
          { name: 'Stats', path: '/stats/' },
          { name: 'EGS POVs', path: '/stats/pov/' },
          { name: pov.title, path: `/stats/pov/${povId}/` },
        ]),
      ]
    : null;

  return (
    <>
      {jsonLd && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: safeJsonLdStringify(jsonLd) }}
        />
      )}
      <PovDetailClient key={povId} params={Promise.resolve({ povId })} />
    </>
  );
}
