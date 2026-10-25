/**
 * The sitewide notice shown directly under the masthead.
 *
 * This is the one place to edit when the club has something to say across the whole site.
 * Set `enabled` to true to put the bar up; set it to false to take it down. The bar is plain
 * text with one optional link, so keep it to a sentence or two rather than a second page.
 */
export interface SiteAnnouncement {
  /** Whether the bar is shown on every page. */
  enabled: boolean;
  /** The notice itself. Plain text, one or two sentences. */
  message: string;
  /** Optional destination. Internal paths and absolute http(s) URLs are supported. */
  href?: string;
  /** Text for the link. Defaults to "Read more". */
  linkLabel?: string;
}

export const SITE_ANNOUNCEMENT: SiteAnnouncement = {
  enabled: false,
  message: 'Add your sitewide announcement here.',
  href: '/changelog/',
  linkLabel: 'Read more',
};
