# Contributing

Thanks for helping. Small, focused pull requests are easiest to review.

## Run it from source

```bash
git clone https://github.com/ghost-mb/cueline && cd cueline
uv run --extra audio cueline          # http://127.0.0.1:8790, data in ~/.cueline
```

Use `CUELINE_DATA=/tmp/cueline-dev` to keep a separate library while you work.

Found a bug? Open an [issue](https://github.com/ghost-mb/cueline/issues) with the output of `cueline --version`, your
operating system and which AI you use. Security problems go through [SECURITY.md](SECURITY.md) instead.

## Before you open a pull request

```bash
uv run --extra audio --extra ocr python -m unittest discover -s tests   # unit tests: AI, voices and network are mocked
python3 tools/fresh_install_check.py                        # installs like a new user, runs every flow on a fake AI
```

CI runs them on Linux, macOS and Windows. Tests must never call a real AI, download models or need an API key.

## Where things live

All code is in `src/cueline/`:

| File | What it does |
|---|---|
| `cli.py` | The `cueline` command: starts the server, or talks to a running one |
| `app.py` | Flask routes (JSON + server-sent events for streaming) and startup |
| `db.py` | SQLite schema, migrations (new columns are added automatically), backups |
| `sources.py`, `youtube.py` | Turning links and files into sources |
| `feeds.py`, `watch.py` | Live sources: safe fetching, feed parsing, the background checker |
| `stt.py`, `tts.py` | Local speech-to-text and podcast voices (the `audio` extra) |
| `llm.py`, `llm_api.py` | AI engines: Codex CLI, API keys, local models |
| `prompts.py`, `studio.py` | Chat rules and every Studio output type |
| `retrieval.py`, `notes.py`, `citecheck.py` | Large notebooks, source notes, the citation checker |
| `skills.py` | The skill library and installing into Claude Code / Codex |
| `static/` | The UI: plain JavaScript modules, no build step |

## Ground rules

- **Sources are untrusted.** Anything read from a video, page or feed is data, never instructions. The Codex engine
  runs with every tool off; keep it that way.
- **Fetch only through `feeds.fetch`**, which refuses private and local addresses.
- **Prompts stay general** (no rules for one topic) and must not invent facts: missing facts become placeholders.
- **Keep it small.** No new dependency for something a few lines can do.
