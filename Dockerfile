# VN Club Frontend Dockerfile
# Multi-stage Bun-only build for optimized production image.
# Node.js and npm are not installed in any stage. The Bun base image provides a
# `node` compatibility symlink that points at Bun; it is not a Node.js runtime.

# Stage 1: Dependencies
FROM oven/bun:1.4.3-alpine AS deps
WORKDIR /app

# Native modules (sharp, Next SWC) on Alpine.
RUN apk add --no-cache libc6-compat libstdc++

COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# Stage 2: Builder
FROM deps AS builder
WORKDIR /app

COPY . .

# Build arguments for environment variables needed at build time
ARG NEXT_PUBLIC_VNDB_STATS_API
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_UMAMI_URL
ARG NEXT_PUBLIC_UMAMI_WEBSITE_ID
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY

ENV NEXT_PUBLIC_VNDB_STATS_API=$NEXT_PUBLIC_VNDB_STATS_API
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_UMAMI_URL=$NEXT_PUBLIC_UMAMI_URL
ENV NEXT_PUBLIC_UMAMI_WEBSITE_ID=$NEXT_PUBLIC_UMAMI_WEBSITE_ID
ENV NEXT_PUBLIC_TURNSTILE_SITE_KEY=$NEXT_PUBLIC_TURNSTILE_SITE_KEY
ENV NEXT_TELEMETRY_DISABLED=1

RUN bun run prebuild:prod
RUN --mount=type=cache,target=/app/.next/cache bun run build

# Stage 3: Runner
FROM oven/bun:1.4.3-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# wget is used by the compose health check; the other two are runtime
# dependencies of the native modules traced into the standalone output.
RUN apk add --no-cache wget libc6-compat libstdc++

# Create non-root user for security. Compose adds group 1000 so this user can
# write the host-owned ./public/vndb-images bind mount.
RUN addgroup -S -g 1001 bunapp && adduser -S -u 1001 -G bunapp bunapp

# Copy built application
COPY --from=builder /app/public ./public
COPY --from=builder --chown=bunapp:bunapp /app/.next/standalone ./
COPY --from=builder --chown=bunapp:bunapp /app/.next/static ./.next/static

# VNDB images live inside the site's public directory so host Angie can serve
# them directly. Docker-compose bind-mounts ./public/vndb-images here.
# /app/public is writable so the image route can also cache placeholder.webp.
ENV VNDB_CACHE_DIR=/app/public/vndb-images
RUN mkdir -p /app/public/vndb-images && chown -R bunapp:bunapp /app/public

USER bunapp

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["bun", "server.js"]
