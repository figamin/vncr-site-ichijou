import Link from '@/components/Link';
import { SITE_ANNOUNCEMENT } from '@/lib/site-announcement';

const EXTERNAL_URL = /^https?:\/\//i;

/**
 * The sitewide notice under the masthead.
 *
 * Rendered inside the page's top padding so the fixed header does not cover it. It is a plain
 * bar rather than a floating panel: an announcement that follows the reader down the page
 * competes with the nav and with whatever the page is for.
 */
export function SiteAnnouncement() {
  const { enabled, message, href, linkLabel } = SITE_ANNOUNCEMENT;
  if (!enabled) return null;

  return (
    <aside className="site-announce" aria-label="Site announcement">
      <div className="container mx-auto max-w-7xl px-4">
        <div className="site-announce-row">
          <span className="site-announce-mark" aria-hidden="true" />
          <p className="site-announce-text">
            {message}
            {href && (
              <>
                {' '}
                {EXTERNAL_URL.test(href) ? (
                  <a
                    href={href}
                    className="site-announce-link"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {linkLabel ?? 'Read more'}
                  </a>
                ) : (
                  <Link href={href} className="site-announce-link">
                    {linkLabel ?? 'Read more'}
                  </Link>
                )}
              </>
            )}
          </p>
        </div>
      </div>
    </aside>
  );
}
