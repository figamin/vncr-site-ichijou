import { NextRequest, NextResponse } from 'next/server';

/**
 * Same-origin proxy for the VNDB stats backend.
 *
 * The browser only ever talks to the site's own origin. Next forwards the
 * request to the backend server-side, which keeps loopback/private API URLs out
 * of the client bundle and avoids Chrome's Local Network Access permission
 * prompt. It also means no second public port is needed.
 */

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const HOP_BY_HOP_HEADERS = [
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
];

const RESPONSE_HEADERS_TO_DROP = [
  // fetch() decompresses the body but can leave these describing the old shape.
  'content-encoding',
  'content-length',
  'transfer-encoding',
  'connection',
  'keep-alive',
];

function backendBase(): string | null {
  const base = process.env.API_URL_INTERNAL || process.env.NEXT_PUBLIC_VNDB_STATS_API;
  return base ? base.replace(/\/$/, '') : null;
}

type RouteContext = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: RouteContext) {
  const base = backendBase();
  if (!base) {
    return NextResponse.json({ detail: 'Backend API is not configured' }, { status: 503 });
  }

  const { path } = await context.params;
  if (path.some((segment) => segment === '..' || segment === '.' || segment.includes('\\'))) {
    return NextResponse.json({ detail: 'Invalid path' }, { status: 400 });
  }

  const target = new URL(base);
  // FastAPI distinguishes paths by their trailing slash (e.g.
  // /api/v1/vn/search/ vs /api/v1/vn/{id}), so preserve it.
  const trailingSlash = request.nextUrl.pathname.endsWith('/');
  target.pathname = `${target.pathname.replace(/\/$/, '')}/${path
    .map(encodeURIComponent)
    .join('/')}${trailingSlash ? '/' : ''}`;
  target.search = request.nextUrl.search;

  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP_HEADERS) headers.delete(name);
  // Ask for an uncompressed body so the response headers we return match it.
  headers.delete('accept-encoding');
  headers.delete('content-length');

  // Keep per-visitor IPs so backend rate limits apply to the visitor, not to Next.
  const clientIp = request.headers.get('x-forwarded-for') || request.headers.get('x-real-ip');
  if (clientIp) {
    headers.set('x-forwarded-for', clientIp);
    headers.set('x-real-ip', clientIp);
  }

  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      // Follow any trailing-slash redirect inside the proxy instead of leaking
      // the backend URL to the browser.
      redirect: 'follow',
      cache: 'no-store',
      signal: request.signal,
    });

    const responseHeaders = new Headers(upstream.headers);
    for (const name of RESPONSE_HEADERS_TO_DROP) responseHeaders.delete(name);

    return new NextResponse(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json({ detail: 'Backend API is unreachable' }, { status: 502 });
  }
}

export async function GET(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export async function HEAD(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export async function PUT(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export async function OPTIONS(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}
