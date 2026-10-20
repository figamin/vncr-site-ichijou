/**
 * Application configuration utilities.
 *
 * Centralizes environment variable access and provides fail-fast behavior
 * when required configuration is missing.
 */

/**
 * Browser-side requests go to Next's same-origin proxy
 * (app/api/backend/[...path]/route.ts), never directly to a loopback or
 * private backend address. That keeps Chrome's Local Network Access prompt
 * away and means only the web port has to be exposed publicly.
 *
 * Static export has no server, so next.config.mjs sets this to any configured
 * public API URL instead.
 */
const CLIENT_BACKEND_BASE = process.env.NEXT_PUBLIC_BACKEND_BASE ?? '/api/backend';

/**
 * Get the backend API URL.
 *
 * @throws Error if NEXT_PUBLIC_VNDB_STATS_API is not set
 */
export function getBackendUrl(): string {
  if (typeof window !== 'undefined') {
    if (!CLIENT_BACKEND_BASE) {
      throw new Error('Backend URL is not configured for this build.');
    }
    return CLIENT_BACKEND_BASE;
  }
  // Server-side: prefer internal Docker URL for container-to-container calls
  if (process.env.API_URL_INTERNAL) {
    return process.env.API_URL_INTERNAL.replace(/\/$/, '');
  }
  const url = process.env.NEXT_PUBLIC_VNDB_STATS_API;
  if (!url) {
    throw new Error(
      'NEXT_PUBLIC_VNDB_STATS_API environment variable is required. ' +
        'Set it in .env.local or your deployment environment.'
    );
  }
  return url;
}

/**
 * Get the backend API URL, returning undefined if not set.
 * Use this only for optional features that can work without the backend.
 */
export function getBackendUrlOptional(): string | undefined {
  if (typeof window !== 'undefined') {
    return CLIENT_BACKEND_BASE || undefined;
  }
  if (process.env.API_URL_INTERNAL) {
    return process.env.API_URL_INTERNAL.replace(/\/$/, '');
  }
  return process.env.NEXT_PUBLIC_VNDB_STATS_API;
}

/**
 * Check if backend API is configured.
 */
export function isBackendConfigured(): boolean {
  if (typeof window !== 'undefined') {
    return !!CLIENT_BACKEND_BASE;
  }
  return !!(process.env.API_URL_INTERNAL || process.env.NEXT_PUBLIC_VNDB_STATS_API);
}

/**
 * True when a URL is an absolute public http(s) URL, i.e. safe to show to
 * visitors (not localhost or a private network address).
 */
export function isPublicHttpUrl(value: string | undefined): value is string {
  if (!value) return false;
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;

    const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, '');
    if (host === 'localhost' || host === '::1' || host.endsWith('.local')) return false;
    if (/^f[cd][0-9a-f]{2}:/i.test(host) || /^fe[89ab][0-9a-f]:/i.test(host)) return false;

    const v4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
    if (!v4) return true;

    const a = Number(v4[1]);
    const b = Number(v4[2]);
    return !(
      a === 10 ||
      a === 127 ||
      (a === 192 && b === 168) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 169 && b === 254)
    );
  } catch {
    return false;
  }
}
