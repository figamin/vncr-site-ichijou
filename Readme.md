# VN Club

> **This project has moved.** vnclub.org is offline, and the codebase is now maintained by figamin. The site is run at vnclub.figamin.net for the time being.

VN Club is a free, open source site about Japanese visual novels.

## Features

- **Browse** - Search and filter the whole VNDB catalogue, with pages for titles, characters, staff, voice actors, producers, tags and traits
- **Recommendations** - Drawn from your VNDB list, filtered by platform, year, reading difficulty, studio or staff. Each title shows the signals that picked it and the score they expect you to give
- **Stats** - Your reading counted, compared against every public list, and set beside the figures for the database as a whole
- **Rankings** - Standing leaderboards drawn from the whole vote record, plus a builder for any slice you can describe
- **Trends** - What the community is reading now, and which way it moved
- **Guides** - Setup guides for the tools people read with, including Textractor, Yomitan, Anki, JL and OwOCR
- **Reading club** - VN of the Month and Season, a weekly read-aloud session, an events calendar and every past pick
- **News** - An aggregated feed, and upcoming Japanese releases
- **Word of the Day** - A daily word with readings, pitch accent, kanji breakdown and example sentences taken from visual novels
- **VN of the Day** - A daily title with its score, developer, tags and cover
- **Things to play with** - Tier list maker, 3x3 collage, roulette, higher or lower, a random picker and a kana quiz
- **Discord bot** - The daily title and word, the events calendar, film nights and the reading club
- Full-text search, dark mode, and an English or Japanese interface

## Running your own copy

Forks are welcome under the license below.

Guide content lives in `content/guides/` as MDX files. The site is built with Next.js and Tailwind CSS, with a FastAPI + PostgreSQL backend that imports VNDB's public data dumps.

```bash
# Configuration (required: the frontend reads the backend URL from .env.local)
cp .env.example .env.local
cp vndb-stats-backend/.env.example vndb-stats-backend/.env

# Frontend development (also starts the Docker backend and imports data if needed)
bun install
bun run dev

# Backend (Docker)
bun run api:dev

# Both together
bun run dev:all

# Local production build + server (also starts the Docker backend)
bun run prod
```

The frontend Dockerfile is Bun-only: `oven/bun:1.4.3-alpine` installs with
`bun install --frozen-lockfile`, builds with `bun run build`, and runs the
standalone server with `bun server.js`. Node.js and npm are not installed.

## Data Sources

- [VNDB](https://vndb.org/) - Visual novel data, tags, characters, and cover images
- [Jiten.moe](https://jiten.moe/) - Word frequency data, reading statistics, and example sentences
- [KanjiAPI](https://kanjiapi.dev/) - Kanji details, readings, and compound words
- [Jisho.org](https://jisho.org/) - JLPT levels and dictionary data
- [Tatoeba](https://tatoeba.org/) - Bilingual example sentences
- [JMdict/KANJIDIC](https://www.edrdg.org/) - Dictionary data from the Electronic Dictionary Research and Development Group

## License

[GNU Affero General Public License (AGPL-3.0)](https://www.gnu.org/licenses/agpl-3.0.en.html)
