<h1 align="center">Cueline</h1>

<p align="center"><b>Your feeds in, cited content out.</b><br>
An open-source NotebookLM for creators. It follows the YouTube channels, blogs and podcasts you learn from<br>
and turns them into posts, scripts, landing pages, podcasts and agent skills.<br>
Answers link to the exact second or page they came from.</p>

<p align="center">
  <a href="https://github.com/ghost-mb/cueline/actions/workflows/ci.yml"><img alt="tests" src="https://github.com/ghost-mb/cueline/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11-3.14" src="https://img.shields.io/badge/python-3.11--3.14-blue">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Runs on your computer" src="https://img.shields.io/badge/runs%20on-your%20computer-orange">
</p>

![Demo: a notebook with two NASA videos, a cited answer, the source passage at 0:52, a LinkedIn post with its fact check, and a SKILL.md](.github/demo.gif)

Two NASA videos in, then a cited answer, a LinkedIn post and an agent skill out. More outputs (a newsletter too) are in
[`examples/`](examples/), unedited, with timestamps you can click.

## Why Cueline

Research notebooks wait for you to feed them. Cueline goes and gets it:

- **Sources arrive by themselves.** Follow a YouTube channel, playlist or search, a blog, newsletter or podcast.
  New items land in an inbox with a summary, a fit score and three content ideas.
- **Claims are traceable.** Answers cite the exact passage or timestamp, and drafts come with a fact check. A built-in
  checker (no AI) flags citations to a moment that doesn't exist and quotes that aren't word for word. The AI is told
  to leave a placeholder when a fact is missing, not to invent one.
- **It makes things you publish, not just notes.** Short and long video scripts, posts, carousels, threads,
  newsletters, articles, landing pages, slide decks, podcasts with two hosts, infographics, in your brand's voice.
- **It teaches your agents.** Turn any notebook into a `SKILL.md` and install it into Claude Code or Codex in one click.
- **It stays on your laptop.** Your library, keys and outputs live in `~/.cueline`. No account, no server, and Cueline itself sends no telemetry.

## Quickstart

Open a terminal and paste two lines. The first installs [uv](https://docs.astral.sh/uv/), a small Python installer
that also fetches a compatible Python for you.

**macOS or Linux** (Terminal):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install git+https://github.com/ghost-mb/cueline
```

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install git+https://github.com/ghost-mb/cueline
```

Then start it (open a new terminal first if `cueline` isn't found):

```bash
cueline
```

Open **http://127.0.0.1:8790**. On first start Cueline asks which AI to use:

| AI | What you need |
|---|---|
| **Your ChatGPT plan** | OpenAI's [Codex CLI](https://github.com/openai/codex): `brew install --cask codex` (Mac) or `npm install -g @openai/codex`, then run `codex` once and choose **Sign in with ChatGPT**. No API bill. |
| **Claude, OpenAI, Gemini, OpenRouter** | Paste an API key in the app. It is stored only on your computer. |
| **A local model** | [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai) running on your computer. Nothing leaves your machine. Small models write weaker drafts than the big cloud ones. |

Two optional add-ons also run on your computer: `audio` for podcasts and transcription (+~400 MB), `ocr` for
scanned PDFs (+~130 MB). Pick one or both:

```bash
uv tool install --force "cueline[audio,ocr] @ git+https://github.com/ghost-mb/cueline"
```

## Your first notebook

1. Click **Create notebook**, then **Add sources** and paste a YouTube link. The other tabs take a website, files,
   pasted text, a live source or another notebook.
2. Ask a question in the chat. Click a citation like `[S1 2:14]` to open that moment in the source.
3. In **Studio**, pick an output such as **Content creation** → Social post → Educational, and generate it. It appears
   with a fact check underneath.
4. To keep the notebook current: **Add sources** → **Follow (live)**, and paste a channel, blog or podcast link. Its 3
   newest items land in **Updates** in the top bar. Add the ones you want as sources.

## What each part does

![Cueline home: follow sources, ask with citations, create in Studio, stay current](.github/home.jpg)

![A new notebook: add sources on the left, chat in the middle, Studio outputs on the right](.github/notebook.jpg)

### Sources

Everything the AI is allowed to use. Switch a source off to leave it out of answers and outputs.

| You add | Cueline reads |
|---|---|
| YouTube video (up to 10 links per paste) | Its captions, or transcribes the audio on your computer when there are none (`audio` add-on) |
| Website or blog post | The page's main text. A site that blocks reading gives a clear error and saves nothing. |
| PDF, DOCX, TXT, MD | The text. Scanned PDF pages need the `ocr` add-on. |
| Audio file | Transcribes it on your computer (`audio` add-on) |
| Pasted text | As is |
| Another notebook | Its sources, read live each time you ask |

### Live sources and the Updates inbox

Follow a YouTube channel, playlist or search, an RSS/Atom blog or newsletter, or a podcast. Sources are checked every 6
hours **while Cueline is running**, and new items land in **Updates** with a summary, a fit score for that notebook and
three content ideas.

- Add an item as a source, or dismiss it. Each live source has a switch to add new items automatically (10 a day at
  most).
- A new follow starts with its 3 newest items, not the whole back catalogue. The AI reads 30 new items a day at most.
- A source that fails 5 checks in a row pauses itself and tells you why. Network outages don't count.
- YouTube channels have a "Transcribe videos without captions" switch (`audio` add-on).

### Chat

Answers use only the sources you have switched on, and arrive as they are written. Citations open the passage, the page
or the moment in the video. If the sources don't say, the answer says so.

**The citation check** runs without AI and marks anything it can't verify: a citation to a source, timestamp or page
that doesn't exist, or a quote of 25+ characters that isn't word for word in your sources.

### Studio

Turns the switched-on sources into something you can use. Add your own instructions to any output.

| Output | What you get | Your choices |
|---|---|---|
| **Content creation** | Paste-ready copy, with a fact check underneath | Format: short video script, long video script, social post, carousel, thread, newsletter, article, 10 hooks and angles, or describe your own. Tone: educational, storytelling, bold, conversational, professional. |
| **Landing page** | A complete HTML page. Offer details missing from the sources become marked placeholders. | Your offer, the offer in your sources, or a free resource |
| **Slide deck** | HTML slides | |
| **Podcast** | An MP3 with a fact check (needs the `audio` add-on). Voices: local Kokoro (free, best in English), OpenAI or Gemini. | Deep dive, quick brief, debate or solo. About 5 or 10 minutes. |
| **Infographic** | An AI image (ChatGPT, OpenAI or Gemini) or an HTML/SVG page with exact text | 4 styles, 3 shapes |
| **Study guide, briefing doc, action plan** | A Markdown report | |
| **Mind map, flashcards, quiz** | Interactive study tools | |
| **Skill** | A `SKILL.md` that teaches your coding agent what the notebook knows | |
| **Brand kit** | A `SKILL.md` that captures how a brand sounds and sells, built from its own material | |

Reports, content, pages, slides, infographics and podcasts can be refined by chat. Each change saves a version you can go
back to (the last 12).

### Projects and brand kits

Projects are optional folders, for example one per brand. Give a project a brand kit and every notebook inside writes in
that voice.

### Skills

A library of `SKILL.md` files: write and preview them, import the ones your agents already have, and install them into
Claude Code (`~/.claude/skills`) or Codex (`~/.codex/skills`). Installing shows a diff and asks first.

### Search, export and backups

- **Search** covers all notebooks, sources and Studio outputs.
- **Export** downloads a notebook as a zip: sources as Markdown, the chat and every Studio output.
- **Backups** of the database run once a day, and the last 7 are kept in `~/.cueline/backups`. To restore, stop Cueline
  and copy one over `~/.cueline/cueline.db`.

### Command line for agents

With Cueline running, you or your coding agent can use a notebook from the terminal:

```bash
cueline notebooks                                 # list notebooks
cueline new "Webb research"                       # create one
cueline add "Webb research" <url or file>         # add a video, page, document or audio file
cueline add "Webb research" --text "..."          # add pasted text
cueline ask "Webb research" "What is the mirror made of?"   # prints the cited answer
```

## Privacy and safety

- The server listens on `127.0.0.1` only and refuses other host names.
- Sources are treated as untrusted data. The ChatGPT/Codex engine runs with every tool turned off (no shell, files,
  web or MCP servers), in an empty folder.
- Generated web pages render in a sandboxed frame: they can't reach Cueline, your files or
  your network. They may load fonts and code libraries from public CDNs (Google Fonts, cdnjs, jsDelivr).
- Feeds and web pages are only fetched from public internet addresses, never your computer or local network. The address
  is checked at the moment of connecting, so DNS tricks (rebinding) can't redirect a fetch. Capped at 5 MB.
- Installing a skill is the only write outside `~/.cueline`, and it always asks first (with a diff and a backup).

To report a problem privately, or to see what Cueline doesn't protect against, read [SECURITY.md](SECURITY.md).

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
| `CUELINE_PORT` | `8790` | Port for the app and the `cueline` commands |
| `CUELINE_WATCH_HOURS` | `6` | How often live sources are checked |
| `CUELINE_FEED_DAILY_CAP` | `30` | New items the AI reads per day |
| `CUELINE_AUTO_ADD_DAILY_CAP` | `10` | Items auto-added as sources per day |
| `CUELINE_STT_MODEL` | `base` | Speech model when none is picked in the app (`base` = Fast, `small` = Accurate) |
| `CUELINE_VOICE_SPEED` | `1.2` | Podcast voice speed (local voices) |
| `CUELINE_CLAUDE_SKILLS_DIR` / `CUELINE_CODEX_SKILLS_DIR` | `~/.claude/skills` / `~/.codex/skills` | Where skills install |
| `CUELINE_CODEX_BIN` | `codex` | Path to the Codex CLI |
| `CUELINE_LOCAL_URL` | `http://localhost:11434/v1` | Local model server (Ollama; LM Studio: `http://localhost:1234/v1`) |

Run on another port with `cueline --port 9000`.

## Development

```bash
git clone https://github.com/ghost-mb/cueline && cd cueline
uv run --extra audio cueline                                    # run from source
uv run --extra audio python -m unittest discover -s tests       # unit tests (AI, voices and network are mocked)
python3 tools/fresh_install_check.py                            # install like a new user, run every flow on a fake AI
```

The code is one Python package (`src/cueline/`: Flask server, SQLite, vanilla JS with no build step). See [CONTRIBUTING.md](CONTRIBUTING.md) for where things live and the ground rules.
On Linux, `scripts/cuelinectl start|stop|status|logs` keeps it running in the background as a systemd user job.

## FAQ

**How is this different from NotebookLM?** NotebookLM is free, polished and hosted by Google. Cueline is open source
and runs on your computer with the AI you choose. It also follows channels and feeds for you, writes publishable
formats (scripts, posts, landing pages) in your brand's voice, checks citations and quotes without AI, and turns
notebooks into skills for your coding agents.

**Is using my ChatGPT plan allowed?** Cueline runs OpenAI's official Codex CLI on your computer with your own login,
with all of Codex's tools turned off, so usage counts toward your plan's Codex limits just like using Codex directly.
Check OpenAI's terms for your plan. An API key or a local model are the alternatives.

**Does my data leave my computer?** Your library stays in `~/.cueline`. The text of the sources you use is sent to
the AI you picked (none if you use a local model), and Cueline fetches the videos, pages and feeds you add.

## Limits

- **Citations:** the checker proves that a citation points to a real moment and that a quote is word for word. It can't
  prove the sentence is a fair reading of that moment, so click through before you publish.
- **Live sources** are only checked while Cueline is running. On Linux, `scripts/cuelinectl` keeps it running.
- **File size:** audio up to 400 MB and 4 hours, other files up to 30 MB. Web pages and feeds up to 5 MB.
- **Scanned PDFs:** up to 30 scanned pages per PDF are read (split longer ones). Handwriting and poor scans may come
  out garbled.
- **Local transcription** sometimes mishears words, names especially. Pick **Accurate** under AI model → Speech to
  text for fewer mistakes (about 3 times slower).
- **Updates inbox:** new videos without captions are summarised from their description, unless you switch on
  "Transcribe videos without captions" for that channel.
- **Very large notebooks** (over ~360k characters of sources) use keyword search to pick passages, so an answer can
  miss a passage that uses different words.
- **Speed and usage** come from your AI plan or key: chat answers take 2-10 seconds, Studio outputs 1-2 minutes.

## License

[MIT](LICENSE)
