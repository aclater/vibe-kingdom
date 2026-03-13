# Vibe Kingdom – Personal Brand Automation with LLMs

This repo contains a small, opinionated automation pipeline for **“vibe coding”** your personal brand using:

- RSS feeds as signal input
- LLMs (either **local via RamaLama** or **cloud via OpenAI**)
- A simple draft → approve → export workflow

The goal is not generic “content marketing,” but an honest, slightly vain, and fun way to:
- stay on top of the domains you care about,
- generate draft posts in your own tone,
- and supercharge your personal brand without hiring a ghostwriter.

## Features

- 📰 Fetches news from configurable RSS feeds into an **ideas** store
- 🧠 Uses an LLM to generate LinkedIn-style post drafts in your voice
- ✅ Simple CLI workflow: `draft` → `approved` → `exported`
- 📦 Exports approved posts as CSV for use with Buffer/Hootsuite/etc.
- ⚙️ Two engines via a single script and `--engine` flag:
  - `--engine cloud` – OpenAI API (default)
  - `--engine local` – local OpenAI-compatible LLM (RamaLama, etc.)

## Repo Layout

- `kingdom.py` – the unified CLI engine (supports both cloud and local LLMs)
- `rss_feeds.txt` – one RSS URL per line; this is your signal input
- `kingdom_state.json` – state file (created at runtime) with ideas + posts
- `.env.example` – example env file for OpenAI API & local LLM settings
- `scripts/install.sh` – convenience installer
- `.gitignore` – ignore state, venv, etc.
- `LICENSE` – MIT

---

## Quick Start – Local LLM (RamaLama)

1. **Install RamaLama** (on Fedora/RHEL-style systems):

   ```bash
   sudo dnf install -y ramalama
   ```

   or via pip:

   ```bash
   pip install -U ramalama
   ```

2. **Run a local model** (example):

   ```bash
   ramalama serve gpt-oss:20b --port 8080
   ```

   This exposes an OpenAI-compatible API at `http://127.0.0.1:8080/v1/chat/completions`.

3. **Clone this repo and set up env**:

   ```bash
   git clone https://github.com/your-user/vibe-kingdom.git
   cd vibe-kingdom

   cp .env.example .env
   # edit .env if needed
   ```

4. **Configure your RSS feeds** in `rss_feeds.txt`:

   ```text
   https://www.fedscoop.com/feed/
   https://www.nextgov.com/rss/all/
   # Add more that match your domain
   ```

5. **Run the pipeline**:

   ```bash
   chmod +x kingdom.py

   ./kingdom.py --engine local fetch-news
   ./kingdom.py --engine local generate-posts --count 6
   ./kingdom.py list-posts --status draft
   ./kingdom.py set-status 1 approved
   ./kingdom.py export-csv --outfile posts_for_scheduler.csv
   ```

   Import the CSV into your social scheduler of choice or just copy/paste.

---

## Cloud Version – OpenAI API

If you want to use OpenAI’s hosted models instead of a local one:

1. Create an API key at: https://platform.openai.com/
2. Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.
3. Install dependencies:

   ```bash
   pip install -U openai feedparser python-dotenv
   ```

4. Run (cloud is the default engine, so no flag needed):

   ```bash
   chmod +x kingdom.py

   ./kingdom.py fetch-news
   ./kingdom.py generate-posts --count 6
   ./kingdom.py list-posts --status draft
   ./kingdom.py set-status 1 approved
   ./kingdom.py export-csv --outfile posts_for_scheduler.csv
   ```

---

## The “Constitution” – Tone & Guardrails

Both engines use an internal “Constitution” to steer the LLM:

- Focus on your actual domain (public-sector IT, AI governance, open source, etc.)
- Avoid partisan politics and non-public details
- Stay concise, pragmatic, and slightly wry
- No emojis or hashtags unless you explicitly ask for them
- Posts are LinkedIn-length (roughly 80–220 words)

You can customize this by editing the `CONSTITUTION` string in `kingdom.py`.

---

## Philosophy: Vibe Coding Your Personal Brand

The idea behind this repo is **vibe coding**: instead of laboriously hand-coding everything up front, you co-design a system with an LLM by describing:

- what you care about,
- how you sound,
- and what tasks you want automated.

The scripts here are one implementation of that idea:
- **RSS** as your input stream,
- **LLMs** as your transformation engine,
- and a small amount of glue code to keep everything organized.

It’s honestly a little vain.  
But it’s also fun, efficient, and surprisingly effective.

You don’t have to outsource your voice to “personal brand agencies” or ghostwriters.  
You can build your own small, weird machine to **amplify** it instead.

---

## License

MIT – do whatever you want, but no warranty. See `LICENSE` for details.
