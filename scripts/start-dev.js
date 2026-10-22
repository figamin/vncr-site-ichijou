#!/usr/bin/env node
/**
 * Development/production startup script
 * Automatically starts Docker containers and imports data if needed.
 *
 * Pass --prod to run `next start` after the backend is ready.
 */

const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT_DIR = path.join(__dirname, '..');
const BACKEND_DIR = path.join(ROOT_DIR, 'vndb-stats-backend');
const GUIDES_DIR = path.join(ROOT_DIR, 'content', 'guides');
const SEARCH_INDEX_PATH = path.join(ROOT_DIR, 'public', 'search-index.json');
const SEARCH_INDEX_SCRIPT = path.join(__dirname, 'generate-search-index.ts');
const IMAGE_DIR = path.join(ROOT_DIR, 'public', 'vndb-images');

// Next loads .env.local in its own child process, but this script needs the same
// values (notably VNDB_CACHE_DIR) before it starts that child.
require('@next/env').loadEnvConfig(ROOT_DIR);

// Load backend .env file for Discord bot token check
const backendEnvPath = path.join(BACKEND_DIR, '.env');
if (fs.existsSync(backendEnvPath)) {
  const envContent = fs.readFileSync(backendEnvPath, 'utf-8');
  envContent.split('\n').forEach((line) => {
    const match = line.match(/^([^#=]+)=(.*)$/);
    if (match && !process.env[match[1]]) {
      process.env[match[1]] = match[2].trim();
    }
  });
}
const API_URL = 'http://localhost:8000';
const IS_PROD = process.argv.includes('--prod');

function log(msg) {
  console.log(`\x1b[36m[startup]\x1b[0m ${msg}`);
}

function logError(msg) {
  console.log(`\x1b[31m[startup]\x1b[0m ${msg}`);
}

function logSuccess(msg) {
  console.log(`\x1b[32m[startup]\x1b[0m ${msg}`);
}

function getDockerStatus() {
  try {
    execSync('docker info', { stdio: 'pipe' });
    return 'ok';
  } catch (e) {
    const stderr = e && e.stderr ? e.stderr.toString() : '';
    return /permission denied/i.test(stderr) ? 'denied' : 'unavailable';
  }
}

function areContainersRunning() {
  try {
    // Check the API service specifically. Checking all services would report
    // "running" while db/redis are up and the API container is still Created.
    const result = execSync('docker compose ps --status running -q api', {
      cwd: BACKEND_DIR,
      encoding: 'utf-8',
    });
    return result.trim().length > 0;
  } catch {
    return false;
  }
}

async function isApiReady(timeoutMs = 2000) {
  try {
    const response = await fetch(`${API_URL}/health`, {
      signal: AbortSignal.timeout(timeoutMs),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForApi(maxAttempts = 30) {
  log('Waiting for API to be ready...');
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const response = await fetch(`${API_URL}/health`);
      if (response.ok) {
        logSuccess('API is ready!');
        return true;
      }
    } catch {
      // API not ready yet
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

async function isDatabasePopulated() {
  try {
    const response = await fetch(`${API_URL}/health/db`);
    if (response.ok) {
      const data = await response.json();
      return data.has_data === true;
    }
  } catch {
    // Ignore
  }
  return false;
}

function startContainers() {
  log('Starting Docker containers...');
  try {
    execSync('docker compose up -d', {
      cwd: BACKEND_DIR,
      stdio: 'inherit',
    });
    return true;
  } catch (e) {
    logError('Failed to start containers');
    return false;
  }
}

function runImport() {
  log('Database is empty. Running initial import (this may take a few minutes)...');
  try {
    execSync('docker compose exec -T api python scripts/initial_import.py', {
      cwd: BACKEND_DIR,
      stdio: 'inherit',
    });
    logSuccess('Import completed!');
    return true;
  } catch (e) {
    logError('Import failed. You can run it manually later with: bun run api:import');
    return false;
  }
}


// The search index is a build artifact, gitignored and rebuilt by `prebuild`, so
// only a production build refreshes it. A dev server serves whatever copy is on
// disk, which silently omits guides added or reworded since it was last written.
function ensureSearchIndex() {
  let newestSource = 0;
  try {
    const sources = fs
      .readdirSync(GUIDES_DIR)
      .filter((f) => f.endsWith('.mdx'))
      .map((f) => path.join(GUIDES_DIR, f))
      .concat([SEARCH_INDEX_SCRIPT]);
    for (const file of sources) {
      newestSource = Math.max(newestSource, fs.statSync(file).mtimeMs);
    }
  } catch {
    // Without a readable guides directory there is nothing to index against.
    return;
  }

  let indexBuiltAt = 0;
  try {
    indexBuiltAt = fs.statSync(SEARCH_INDEX_PATH).mtimeMs;
  } catch {
    // Missing index: fall through and build it.
  }

  if (indexBuiltAt >= newestSource) {
    log('Search index is up to date');
    return;
  }

  log('Rebuilding search index from content/guides...');
  try {
    execSync(`bun "${SEARCH_INDEX_SCRIPT}"`, { cwd: ROOT_DIR, stdio: 'ignore' });
    logSuccess('Search index rebuilt');
  } catch {
    logError('Search index rebuild failed; site search will serve the previous index');
    logError('Rebuild it manually with: bun run generate-search');
  }
}

function startDiscordBot() {
  // Check if DISCORD_BOT_TOKEN is set
  if (!process.env.DISCORD_BOT_TOKEN) {
    log('DISCORD_BOT_TOKEN not set, skipping Discord bot');
    return null;
  }

  log('Starting Discord bot via Docker...');

  // Run the bot inside Docker so it can reach the database on the vndb-net network.
  // PostgreSQL port is not exposed to the host, so running locally won't work.
  const bot = spawn('docker', ['compose', 'up', 'discord-bot'], {
    cwd: BACKEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  bot.stdout.on('data', (data) => {
    const lines = data.toString().trim().split('\n');
    lines.forEach((line) => {
      console.log(`\x1b[35m[discord]\x1b[0m ${line}`);
    });
  });

  bot.stderr.on('data', (data) => {
    const lines = data.toString().trim().split('\n');
    lines.forEach((line) => {
      console.log(`\x1b[35m[discord]\x1b[0m ${line}`);
    });
  });

  bot.on('close', (code) => {
    if (code !== 0 && code !== null) {
      logError(`Discord bot exited with code ${code}`);
    }
  });

  return bot;
}

function startNextServer() {
  log(IS_PROD ? 'Starting production server...' : 'Starting development servers...');

  // A production build already generated the search index via `prebuild`.
  if (!IS_PROD) ensureSearchIndex();

  // The bot is a development convenience; production deployments run it separately.
  const bot = IS_PROD ? null : startDiscordBot();

  // Keep runtime images inside the site tree so host Angie can serve them
  // directly. `.env.local` can override this with VNDB_CACHE_DIR.
  if (!process.env.VNDB_CACHE_DIR) {
    process.env.VNDB_CACHE_DIR = IMAGE_DIR;
  }
  fs.mkdirSync(process.env.VNDB_CACHE_DIR, { recursive: true });
  log(`Image directory: ${process.env.VNDB_CACHE_DIR}`);

  // Run Next directly via node, not through a shell. The cmd.exe a shell wraps it
  // in mangles Ctrl+C on Windows (closing the whole terminal); the bin as a plain
  // child can be torn down cleanly.
  const nextBin = require.resolve('next/dist/bin/next');
  const nextArgs = IS_PROD ? ['start', '-p', '3000'] : ['dev', '--turbo', '-p', '3000'];
  const next = spawn(process.execPath, [nextBin, ...nextArgs], {
    stdio: 'inherit',
    cwd: path.join(__dirname, '..'),
  });

  let shuttingDown = false;
  function shutdown() {
    if (shuttingDown) return; // Ctrl+C hits every process on the console; act once
    shuttingDown = true;
    if (bot) bot.kill();
    next.kill();
  }

  next.on('close', (code) => {
    if (bot) bot.kill();
    process.exit(code ?? 0);
  });

  // Stop the children and let this process exit normally so the shell regains
  // control, rather than the default handler tearing it down.
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

async function main() {
  console.log('\n');
  log(`Starting VN Club ${IS_PROD ? 'production' : 'development'} environment...\n`);

  // If the API is already answering, this shell does not need Docker access.
  const backendAlreadyUp = await isApiReady();
  if (!backendAlreadyUp) {
    // Check Docker
    const docker = getDockerStatus();
    if (docker !== 'ok') {
      if (docker === 'denied') {
        logError('Docker is running, but this user cannot access /var/run/docker.sock.');
        logError('Add yourself to the docker group, then log out and back in:');
        logError('  sudo usermod -aG docker $USER');
      } else {
        logError('Docker is not running!');
        logError('Please start Docker Desktop and try again.');
      }
      process.exit(1);
    }

    // Start containers if not running
    if (!areContainersRunning()) {
      if (!startContainers()) {
        process.exit(1);
      }
    } else {
      log('Docker containers already running');
    }
  } else {
    log('Backend is already running');
  }

  // Wait for API
  const apiReady = backendAlreadyUp || await waitForApi();
  if (!apiReady) {
    logError('API failed to start. Check Docker logs with: docker compose logs -f');
    process.exit(1);
  }

  // Check if data needs to be imported
  const hasData = await isDatabasePopulated();
  if (!hasData) {
    runImport();
  } else {
    log('Database already has data');
  }

  console.log('\n');
  logSuccess('Backend is ready at http://localhost:8000');
  logSuccess('Starting Next.js...\n');

  // Start Next.js
  startNextServer();
}

// Guarded so the individual steps can be exercised without booting the
// containers, which is the only way to check them in isolation.
if (require.main === module) {
  main().catch((e) => {
    logError(e.message);
    process.exit(1);
  });
}

module.exports = { ensureSearchIndex };
