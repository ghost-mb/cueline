<h1 align="center">Cueline</h1>

<p align="center"><b>Your feeds in, cited content out.</b><br>
Follow the channels, blogs and podcasts you learn from. Get posts, scripts, landing pages, podcasts and agent skills,<br>
with every claim linked to the second it was said.</p>

<p align="center">
  <a href="https://github.com/ghost-mb/cueline/actions/workflows/ci.yml"><img alt="tests" src="https://github.com/ghost-mb/cueline/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11-3.14" src="https://img.shields.io/badge/python-3.11--3.14-blue">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Runs locally" src="https://img.shields.io/badge/runs-locally-orange">
</p>

![Cueline home: follow sources, ask with citations, create in Studio, stay current](.github/home.jpg)

## Why Cueline

Research notebooks wait for you to feed them. Cueline goes and gets it:

- **Sources arrive by themselves.** Follow a YouTube channel, playlist or search, a blog, newsletter or podcast.
  New items land in an inbox with a summary, a fit score and three content ideas.
- **Every claim is traceable.** Answers and drafts cite the exact passage or timestamp; a built-in checker (no AI)
  flags quotes and citations that don't match the source. Missing facts become placeholders, never inventions.
- **It makes things you publish, not just notes.** Short and long video scripts, posts, carousels, threads,
  newsletters, articles, landing pages, slide decks, podcasts with two hosts, infographics, in your brand's voice.
- **It teaches your agents.** Turn any notebook into a `SKILL.md` and install it into Claude Code or Codex in one click.
- **It stays on your laptop.** Your library, keys and outputs live in `~/.cueline`. No account, no server, and Cueline itself sends no telemetry.

![A new notebook: add sources on the left, chat in the middle, Studio outputs on the right](.github/notebook.jpg)

## Quickstart

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (macOS or Linux). It picks a compatible Python (3.11-3.14) or downloads one.

```bash
uv tool install git+https://github.com/ghost-mb/cueline
cueline
```

Open **http://127.0.0.1:8790** and pick your AI:

| AI | What you need |
|---|---|
| **ChatGPT plan** | The [Codex CLI](https://github.com/openai/codex), logged in (`codex login`). No API bill. |
| **Claude, OpenAI, Gemini, OpenRouter** | Paste an API key in the app. It is stored only on your computer. |

Podcasts and audio transcription run on your computer too. Add them with the `audio` extra (+~400 MB):

```bash
uv tool install --force "cueline[audio] @ git+https://github.com/ghost-mb/cueline"
```

## What's inside

| | |
|---|---|
| **Sources** | YouTube videos (captions, or the audio transcribed locally when there are none), web pages and blog posts, PDF/DOCX/TXT/MD, audio files and podcast episodes (transcribed locally), pasted text, other notebooks |
| **Live sources** | YouTube channels, playlists and searches, RSS/Atom blogs, newsletters, podcasts. Checked every 6 hours. |
| **Chat** | Streaming answers grounded only in your sources, with clickable citations that open the exact passage or moment |
| **Studio** | Content creation (8 formats × 5 tones, fact check under each piece), landing page, slide deck, podcast, infographic, report, mind map, flashcards, quiz, skill, brand kit. Refine any output by chat; versions are kept. |
| **Brand kits** | Projects hold a brand kit (its voice, offers and phrases) that every notebook inside writes in |
| **Skills** | A library of `SKILL.md` files: write, version, preview, import from your agents, install into `~/.claude/skills` and `~/.codex/skills` |
| **Agent CLI** | `cueline notebooks`, `cueline new`, `cueline add <notebook> <url or file>`, `cueline ask <notebook> "question"` |

## Privacy and safety

- The server listens on `127.0.0.1` only and refuses other host names.
- Sources are treated as untrusted data. The ChatGPT/Codex engine runs with every tool turned off (no shell, files,
  web or MCP servers), in an empty folder.
- Generated web pages render in a sandboxed frame: they can't reach Cueline, your files or
  your network. They may load fonts and code libraries from public CDNs (Google Fonts, cdnjs, jsDelivr).
- Feeds and web pages are only fetched from public internet addresses, never your computer or local network. The address
  is checked at the moment of connecting, so DNS tricks (rebinding) can't redirect a fetch. Capped at 5 MB.
- Installing a skill is the only write outside `~/.cueline`, and it always asks first (with a diff and a backup).

## Updating

```bash
uv tool upgrade cueline
```

Your data in `~/.cueline` is kept, and the database updates itself on the next start.

## Settings

Environment variables, all optional:

| Variable | Default | |
|---|---|---|
| `CUELINE_DATA` | `~/.cueline` | Database, keys, models, backups |
| `CUELINE_WATCH_HOURS` | `6` | How often live sources are checked |
| `CUELINE_FEED_DAILY_CAP` | `30` | New items the AI reads per day |
| `CUELINE_AUTO_ADD_DAILY_CAP` | `10` | Items auto-added as sources per day |
| `CUELINE_STT_MODEL` | `base` | Local speech-to-text model (faster-whisper) |
| `CUELINE_VOICE_SPEED` | `1.2` | Podcast voice speed (local voices) |
| `CUELINE_CLAUDE_SKILLS_DIR` / `CUELINE_CODEX_SKILLS_DIR` | `~/.claude/skills` / `~/.codex/skills` | Where skills install |
| `CUELINE_CODEX_BIN` | `codex` | Path to the Codex CLI |

Run on another port with `cueline --port 9000`.

## Development

```bash
git clone https://github.com/ghost-mb/cueline && cd cueline
uv run --extra audio cueline                                    # run from source
uv run --extra audio python -m unittest discover -s tests       # unit tests (AI, voices and network are mocked)
python3 tools/fresh_install_check.py                            # install like a new user, run every flow on a fake AI
```

The code is one Python package (`src/cueline/`: Flask server, SQLite, vanilla JS with no build step).
On Linux, `scripts/cuelinectl start|stop|status|logs` keeps it running in the background as a systemd user job.

## Limits

- **Videos without captions** are transcribed on your computer (needs the `audio` extra; about 5-10 minutes per hour
  of video). The default speech model is small and fast; `CUELINE_STT_MODEL=small` is more accurate but slower.
  New videos in **Updates** still need captions for their AI summary.
- **Scanned PDFs** (images of text) can't be read yet: there's no OCR.
- **Speed and usage limits** come from your AI plan or key. Studio outputs take 1-2 minutes; chat answers 2-10 seconds.
- **One person per install**, by design: it runs on your computer, not a server.
- **Very large notebooks** (over ~360k characters of sources) use keyword passage search instead of reading everything,
  so answers can miss a passage that uses different words.

## License

[MIT](LICENSE)
