"""Cueline web server: Flask routes + JSON/SSE API for the UI. Started by the `cueline` command → http://127.0.0.1:8790"""
import json
import os
import queue
import random
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, stream_with_context

from . import citecheck
from . import db
from . import feeds
from . import images
from . import tts
from . import notes
from . import prompts
from . import retrieval
from . import skills
from . import sources
from . import studio
from . import stt
from . import watch
from . import llm
from .llm import engine

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = stt.MAX_AUDIO_BYTES + 1024 * 1024  # audio uploads; other files are capped in sources
DEFAULT_TITLE = "Untitled notebook"

# Artifacts run as an opaque origin with no network except fonts/CDN libraries, so a generated page can't call
# Cueline's API or leak anything.
ARTIFACT_CSP = ("default-src 'none'; script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
                "style-src 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
                "font-src https://fonts.gstatic.com data:; img-src data: blob:; media-src data: blob:; "
                "connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-scripts")


def err(message: str, code: int = 400):
    return jsonify({"error": message}), code


def need(row):
    if row is None:
        abort(404)
    return row


@app.errorhandler(404)
def not_found(_):
    return err("Not found", 404)


@app.errorhandler(413)
def too_large(_):
    return err("That file is larger than 30 MB.", 413)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "penguin.linux.test"}  # last one = ChromeOS Linux


@app.before_request
def csrf_guard():
    # DNS rebinding: a web page whose domain points at 127.0.0.1 still sends its own Host header.
    if request.host.rsplit(":", 1)[0] not in LOCAL_HOSTS:
        return err("Cueline only answers on localhost.", 403)
    # Cross-site pages can't set custom headers without a CORS preflight we never approve.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Cueline") != "1":
        return err("Missing X-Cueline header", 403)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    new = db.one("""SELECT COUNT(*) AS n FROM updates WHERE state = 'inbox' AND status = 'ready'""")["n"]
    return jsonify({"ai": engine.status(), "studio_types": studio.catalog(), "updates_new": new,
                    "hermes": sources.HERMES_RAW_DIR.is_dir()})


# ---------- notebooks ----------
@app.get("/api/notebooks")
def list_notebooks():
    return jsonify(db.notebooks())


@app.post("/api/notebooks")
def create_notebook():
    body = request.get_json(silent=True) or {}
    now = db.now()
    project_id = body.get("project_id") or None
    if project_id:
        need(db.one("SELECT id FROM projects WHERE id = ?", (project_id,)))
    nb = db.insert("notebooks", title=(body.get("title") or DEFAULT_TITLE).strip()[:120], emoji="📓",
                   project_id=project_id, created_at=now, updated_at=now)
    return jsonify(nb), 201


_fit_backfill: set[str] = set()


@app.get("/api/notebooks/<nb_id>")
def get_notebook(nb_id):
    nb = need(db.one("SELECT * FROM notebooks WHERE id = ?", (nb_id,)))
    nb["sources"] = db.sources(nb_id)
    nb["messages"] = [citecheck.ensure("messages", m) if m["role"] == "assistant" else m for m in db.messages(nb_id)]
    nb["studio"] = db.studio_items(nb_id)
    for item in nb["studio"]:  # new or changed outputs: run the citation/placeholder check once (cached after)
        if item["status"] == "done" and item.get("checks") is None and item["kind"] in studio.TYPES:
            full = db.one("SELECT * FROM studio_items WHERE id = ?", (item["id"],))
            item["checks"] = citecheck.ensure("studio_items", full, studio.check_format(full)).get("checks")
    nb["project"] = db.one("SELECT id, name, emoji, brand_skill_id FROM projects WHERE id = ?", (nb["project_id"],)) \
        if nb.get("project_id") else None
    nb["follows"] = db.follows(nb_id)
    # what chat/Studio would read (enabled + linked sources) vs the full-read limit; above it, passage search is used
    nb["size"] = {"chars": sum(len(s["content"]) for s in db.context_sources(nb_id)), "limit": prompts.SOURCE_BUDGET_CHARS}
    if nb.get("studio_fit") is None and nb["sources"] and nb_id not in _fit_backfill:
        _fit_backfill.add(nb_id)  # notebooks from before studio_fit: fill it once, in the background
        threading.Thread(target=refresh_notebook_guide, args=(nb_id,), daemon=True).start()
    for s in nb["sources"]:
        if s["kind"] == "notebook":  # live link: show what it currently brings in
            linked = (s.get("meta") or {}).get("notebook_id")
            s["linked"] = db.one("""SELECT id, title, emoji, (SELECT COUNT(*) FROM sources x WHERE x.notebook_id = n.id
                                    AND x.enabled = 1) AS source_count FROM notebooks n WHERE id = ?""", (linked,))
    return jsonify(nb)


@app.patch("/api/notebooks/<nb_id>")
def update_notebook(nb_id):
    need(db.one("SELECT id FROM notebooks WHERE id = ?", (nb_id,)))
    body = request.get_json(silent=True) or {}
    fields = {k: str(body[k]).strip()[:120] for k in ("title", "emoji") if body.get(k)}
    if "project_id" in body:
        if body["project_id"]:
            need(db.one("SELECT id FROM projects WHERE id = ?", (body["project_id"],)))
        fields["project_id"] = body["project_id"] or None
    return jsonify(db.update("notebooks", nb_id, **fields, updated_at=db.now()))


@app.delete("/api/notebooks/<nb_id>")
def delete_notebook(nb_id):
    for s in db.query("SELECT id, meta FROM sources WHERE kind = 'notebook'"):
        if (s.get("meta") or {}).get("notebook_id") == nb_id:  # links from other notebooks to this one
            db.delete("sources", s["id"])
    for item in db.query("SELECT id FROM studio_items WHERE notebook_id = ?", (nb_id,)):
        studio.delete_images(item["id"])  # the cascade removes rows, not their podcast/image files
    for src in db.query("SELECT meta FROM sources WHERE notebook_id = ?", (nb_id,)):
        _drop_audio(src)
    db.delete("notebooks", nb_id)
    retrieval.prune()  # its sources are gone (cascade); drop their search index entries
    return "", 204


# ---------- sources ----------
def _add_source(nb_id: str, record: dict):
    src = db.insert("sources", notebook_id=nb_id, created_at=db.now(), enabled=1, **record)
    db.touch_notebook(nb_id)
    if (src["meta"] or {}).get("youtube_audio"):
        src = stt.queue(src["id"])  # guides run when the transcript lands (stt.on_done)
    else:
        threading.Thread(target=_guides, args=(nb_id, src["id"]), daemon=True).start()
    src.pop("content", None)
    return jsonify(src), 201


def _guides(nb_id: str, source_id: str, refresh: bool = True):
    """Background: per-source guide, then refresh the notebook's title/emoji/summary/suggestions."""
    try:
        src = db.one("SELECT * FROM sources WHERE id = ?", (source_id,))
        guide = engine.generate_json(prompts.GUIDE_SYSTEM, prompts.source_guide_prompt(src), prompts.SOURCE_GUIDE_SCHEMA)
        guide["notes"] = notes.clean(src, guide.get("notes"))
        guide["notes_v"] = notes.NOTES_V
        db.update("sources", source_id, guide=guide)
        if len(notes.segments(src["content"])) > 1:  # long source: notes for the rest, then save again
            guide["notes"] += notes.extra_notes(src, engine.generate_json)
            db.update("sources", source_id, guide=guide)
        if refresh:
            refresh_notebook_guide(nb_id)
    except Exception as exc:
        app.logger.warning("guide generation failed: %s", exc)


watch.on_source_added = _guides  # adding a live item (by hand or auto) writes its guides
stt.on_done = _guides  # a finished transcript gets fresh guides + notes


def refresh_notebook_guide(nb_id: str):
    nb = db.one("SELECT * FROM notebooks WHERE id = ?", (nb_id,))
    srcs = [s for s in db.sources(nb_id, with_content=True) if s["kind"] != "notebook"] + \
        [s for s in db.context_sources(nb_id) if s["notebook_id"] != nb_id]
    if not nb or not srcs:
        return
    guide = engine.generate_json(prompts.GUIDE_SYSTEM, prompts.notebook_guide_prompt(srcs, studio.FIT),
                                 prompts.notebook_guide_schema(studio.FIT))
    fit = [k for k in dict.fromkeys(guide.get("studio_fit") or []) if k in studio.FIT]
    fields = {"summary": guide["summary"], "suggestions": guide["suggestions"][:3], "studio_fit": fit[:6] or None}
    if nb["title"] == DEFAULT_TITLE:
        fields["title"] = guide["title"][:120]
        fields["emoji"] = (guide["emoji"] or "📓")[:8]
    db.update("notebooks", nb_id, **fields)


@app.post("/api/notebooks/<nb_id>/seen")
def mark_notebook_seen(nb_id):
    """The user opened the notebook: its auto-added sources stop counting as new."""
    with db.connect() as conn:
        conn.execute("UPDATE sources SET is_new = 0 WHERE notebook_id = ? AND is_new = 1", (nb_id,))
    return "", 204


@app.post("/api/sources/seen")
def mark_all_seen():
    with db.connect() as conn:
        conn.execute("UPDATE sources SET is_new = 0 WHERE is_new = 1")
    return "", 204


@app.post("/api/notebooks/<nb_id>/sources")
def add_source(nb_id):
    need(db.one("SELECT id FROM notebooks WHERE id = ?", (nb_id,)))
    body = request.get_json(silent=True) or {}
    kind = body.get("type")
    try:
        if kind == "youtube":
            record = sources.from_youtube(body.get("url", ""))
            dup = db.one("SELECT id FROM sources WHERE notebook_id = ? AND url = ?", (nb_id, record["url"]))
            if dup:
                return err("This video is already in the notebook.", 409)
        elif kind == "web":
            record = sources.from_web(body.get("url", ""))
            if db.one("SELECT id FROM sources WHERE notebook_id = ? AND url = ?", (nb_id, record["url"])):
                return err("This page is already in the notebook.", 409)
        elif kind == "text":
            record = sources.from_text(body.get("title", ""), body.get("text", ""))
        elif kind == "hermes":
            record = sources.from_hermes_transcript(body.get("file", ""))
        elif kind == "notebook":
            linked = db.one("SELECT id, title, summary FROM notebooks WHERE id = ?", (body.get("notebook_id"),))
            if not linked or linked["id"] == nb_id:
                return err("Pick another notebook.")
            for s in db.query("SELECT meta FROM sources WHERE notebook_id = ? AND kind = 'notebook'", (nb_id,)):
                if (s.get("meta") or {}).get("notebook_id") == linked["id"]:
                    return err("That notebook is already linked.", 409)
            # Its sources are read live at question time (db.context_sources); the guide is the linked summary.
            src = db.insert("sources", notebook_id=nb_id, created_at=db.now(), enabled=1, kind="notebook",
                            title=linked["title"], url=None, content="", word_count=0,
                            meta={"notebook_id": linked["id"]},
                            guide={"summary": linked["summary"] or f"Linked notebook: {linked['title']}", "key_topics": []})
            db.touch_notebook(nb_id)
            threading.Thread(target=refresh_notebook_guide, args=(nb_id,), daemon=True).start()
            return jsonify(src), 201
        else:
            return err("Unknown source type")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(str(exc), 502)
    return _add_source(nb_id, record)


# ---------- several links at once (YouTube videos and web pages) ----------
# One background job per paste: links are read one after another with a pause in between (site rate limits),
# and each source guide is written before the next video starts (AI rate limits). Jobs live in memory only.
BATCH_MAX = 10
BATCH_DELAY = float(os.environ.get("CUELINE_BATCH_DELAY", "8"))  # seconds between videos (+ up to 4 s random)
ANY_LINK = re.compile(r"https?://[^\s<>\"']+")
YT_LINK = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|live/)|youtu\.be/)[\w-]{6,}[^\s,]*")
_batches: dict[str, dict] = {}


def _batch_worker(job: dict):
    nb_id = job["notebook_id"]
    for i, it in enumerate(job["items"]):
        if i:
            time.sleep(BATCH_DELAY + random.uniform(0, 4))
        it["state"] = "working"
        for attempt in (1, 2):
            try:
                record = sources.from_youtube(it["url"]) if it["kind"] == "youtube" else sources.from_web(it["url"])
                it["title"] = record["title"]
                if db.one("SELECT id FROM sources WHERE notebook_id = ? AND url = ?", (nb_id, record["url"])):
                    it["state"] = "skipped"
                    it["error"] = "Already in the notebook, so not added again."
                    break
                src = db.insert("sources", notebook_id=nb_id, created_at=db.now(), enabled=1, **record)
                db.touch_notebook(nb_id)
                it["state"] = "added"
                if (src["meta"] or {}).get("youtube_audio"):
                    stt.queue(src["id"])  # no captions: transcribed locally, guides follow
                else:
                    _guides(nb_id, src["id"], refresh=False)  # waits for the guide: spaces out the AI calls too
                break
            except Exception as exc:
                msg = str(exc)
                if attempt == 1 and re.search(r"429|too many requests|rate", msg, re.I):
                    it["error"] = "The site asked to slow down; retrying in 60 s."
                    time.sleep(60)
                    continue
                it["state"], it["error"] = "failed", msg[:300] + " Not added to the notebook."
                break
    if any(it["state"] == "added" for it in job["items"]):
        try:
            refresh_notebook_guide(nb_id)
        except Exception as exc:
            app.logger.warning("notebook guide after batch failed: %s", exc)
    job["done"] = True


@app.post("/api/notebooks/<nb_id>/sources/batch")
def add_sources_batch(nb_id):
    need(db.one("SELECT id FROM notebooks WHERE id = ?", (nb_id,)))
    text = str((request.get_json(silent=True) or {}).get("text") or "")
    kind = (request.get_json(silent=True) or {}).get("kind")  # "youtube" tab: videos only; "web" tab: any page
    pattern = YT_LINK if kind == "youtube" else ANY_LINK
    urls = list(dict.fromkeys(m.group(0).rstrip(").],;") for m in pattern.finditer(text)))
    if not urls:
        return err("No links found. Paste one link per line." if kind != "youtube"
                   else "No YouTube video links found. Paste one link per line.")
    if len(urls) > BATCH_MAX:
        return err(f"That's {len(urls)} links. Add up to {BATCH_MAX} at a time.")
    job = {"id": db.new_id(), "notebook_id": nb_id, "done": False, "delay_s": BATCH_DELAY,
           "items": [{"url": u, "kind": "youtube" if YT_LINK.fullmatch(u) else "web", "state": "waiting", "title": "",
                      "error": ""} for u in urls]}
    _batches[job["id"]] = job
    threading.Thread(target=_batch_worker, args=(job,), daemon=True).start()
    return jsonify(job), 202


@app.get("/api/batches/<job_id>")
def get_batch(job_id):
    return jsonify(need(_batches.get(job_id)))


@app.post("/api/notebooks/<nb_id>/upload")
def upload_source(nb_id):
    need(db.one("SELECT id FROM notebooks WHERE id = ?", (nb_id,)))
    f = request.files.get("file")
    if not f or not f.filename:
        return err("Choose a file to upload.")
    if Path(f.filename).suffix.lower() in stt.AUDIO_EXTENSIONS:  # saved, then transcribed on this computer
        try:
            stt.need_audio("faster_whisper")
        except ValueError as exc:
            return err(str(exc))
        stt.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        name = db.new_id().replace("-", "") + Path(f.filename).suffix.lower()
        f.save(stt.AUDIO_DIR / name)
        src = db.insert("sources", notebook_id=nb_id, created_at=db.now(), enabled=1, kind="audio",
                        title=Path(f.filename).stem.replace("_", " ").strip() or f.filename, url=None,
                        content="(Audio file: the transcript appears here when it is ready.)", word_count=0,
                        meta={"filename": f.filename, "audio_file": name})
        db.touch_notebook(nb_id)
        src = stt.queue(src["id"])
        src.pop("content", None)
        return jsonify(src), 201
    try:
        record = sources.from_file(f.filename, f.read())
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"Couldn't read that file: {exc}", 422)
    return _add_source(nb_id, record)


@app.post("/api/sources/<src_id>/transcribe")
def transcribe_source(src_id):
    need(db.one("SELECT id FROM sources WHERE id = ?", (src_id,)))
    try:
        src = stt.queue(src_id)
    except ValueError as exc:
        return err(str(exc))
    src.pop("content", None)
    return jsonify(src), 202


@app.get("/api/hermes-transcripts")
def hermes_transcripts():
    return jsonify(sources.hermes_transcripts())


@app.get("/api/sources/<src_id>")
def get_source(src_id):
    return jsonify(need(db.one("SELECT * FROM sources WHERE id = ?", (src_id,))))


@app.patch("/api/sources/<src_id>")
def update_source(src_id):
    need(db.one("SELECT id FROM sources WHERE id = ?", (src_id,)))
    body = request.get_json(silent=True) or {}
    fields = {}
    if "enabled" in body:
        fields["enabled"] = 1 if body["enabled"] else 0
    if body.get("title"):
        fields["title"] = str(body["title"]).strip()[:200]
    src = db.update("sources", src_id, **fields)
    src.pop("content", None)
    return jsonify(src)


def _drop_audio(src: dict | None):
    if src and (src["meta"] or {}).get("audio_file"):  # upload not transcribed yet
        (stt.AUDIO_DIR / Path(src["meta"]["audio_file"]).name).unlink(missing_ok=True)


@app.delete("/api/sources/<src_id>")
def delete_source(src_id):
    _drop_audio(db.one("SELECT meta FROM sources WHERE id = ?", (src_id,)))
    db.delete("sources", src_id)
    retrieval.forget([src_id])
    return "", 204


# ---------- chat ----------
def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/notebooks/<nb_id>/chat")
def chat(nb_id):
    need(db.one("SELECT id FROM notebooks WHERE id = ?", (nb_id,)))
    body = request.get_json(silent=True) or {}
    question = (body.get("message") or "").strip()
    if not question:
        return err("Type a question first.")
    srcs = db.context_sources(nb_id)
    if not srcs:
        return err("Add or turn on at least one source first.")
    skill = db.one("SELECT * FROM skills WHERE id = ?", (body["skill_id"],)) if body.get("skill_id") else None
    history = db.messages(nb_id)
    user_msg = db.insert("messages", notebook_id=nb_id, role="user", content=question,
                         skill_id=skill["id"] if skill else None, created_at=db.now())
    system = prompts.with_skill(prompts.CHAT_SYSTEM, skill)
    last_q = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    packed, context = retrieval.select(srcs, retrieval.chat_query(question, last_q), expand=retrieval.expand_query)
    if context["mode"] == "retrieval":
        notes.backfill(srcs)  # sources from before notes existed get them in the background
    prompt = prompts.chat_prompt(packed, history, question, context)
    events: queue.Queue = queue.Queue()

    def work():
        try:
            text = engine.generate(system, prompt, on_delta=lambda d: events.put(("delta", d)), effort="low")
            msg = db.insert("messages", notebook_id=nb_id, role="assistant", content=text,
                            skill_id=skill["id"] if skill else None, source_ids=[s["id"] for s in srcs],
                            context=context, created_at=db.now())  # coverage + spans
            db.touch_notebook(nb_id)
            events.put(("done", citecheck.ensure("messages", msg)))
        except Exception as exc:
            events.put(("error", str(exc)))

    threading.Thread(target=work, daemon=True).start()

    def stream():
        yield _sse("user", user_msg)
        while True:
            kind, payload = events.get()
            yield _sse(kind, payload)
            if kind in ("done", "error"):
                break

    return Response(stream_with_context(stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/api/notebooks/<nb_id>/messages")
def clear_chat(nb_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM messages WHERE notebook_id = ?", (nb_id,))
    return "", 204


# ---------- studio ----------
@app.post("/api/notebooks/<nb_id>/studio")
def create_studio_item(nb_id):
    need(db.one("SELECT id FROM notebooks WHERE id = ?", (nb_id,)))
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    if kind == "note":
        now = db.now()
        item = db.insert("studio_items", notebook_id=nb_id, kind="note", status="done",
                         title=(body.get("title") or "Note").strip()[:120], content=body.get("content") or "",
                         created_at=now, updated_at=now)
        return jsonify(item), 201
    try:
        item = studio.start(nb_id, kind, body.get("instructions") or "", body.get("skill_id"),
                            body.get("choices") if isinstance(body.get("choices"), dict) else None)
    except ValueError as exc:
        return err(str(exc))
    return jsonify(item), 202


@app.get("/api/studio/<item_id>")
def get_studio_item(item_id):
    item = need(db.one("SELECT * FROM studio_items WHERE id = ?", (item_id,)))
    if item["status"] == "done" and item["kind"] in studio.TYPES:
        citecheck.ensure("studio_items", item, studio.check_format(item))
    return jsonify(item)


@app.patch("/api/studio/<item_id>")
def update_studio_item(item_id):
    need(db.one("SELECT id FROM studio_items WHERE id = ?", (item_id,)))
    body = request.get_json(silent=True) or {}
    fields = {k: body[k] for k in ("title", "content") if k in body}
    return jsonify(db.update("studio_items", item_id, **fields, updated_at=db.now()))


@app.post("/api/studio/<item_id>/refine")
def refine_studio_item(item_id):
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(studio.refine(item_id, str(body.get("change") or ""))), 202
    except LookupError:
        abort(404)
    except ValueError as exc:
        return err(str(exc))


@app.post("/api/studio/<item_id>/restore")
def restore_studio_item(item_id):
    need(db.one("SELECT id FROM studio_items WHERE id = ?", (item_id,)))
    try:
        return jsonify(studio.restore(item_id, int((request.get_json(silent=True) or {}).get("index", -1))))
    except (ValueError, TypeError) as exc:
        return err(str(exc))


@app.delete("/api/studio/<item_id>")
def delete_studio_item(item_id):
    db.delete("studio_items", item_id)
    studio.delete_images(item_id)
    return "", 204


@app.get("/api/studio/<item_id>/view")
def view_artifact(item_id):
    item = need(db.one("SELECT * FROM studio_items WHERE id = ?", (item_id,)))
    if studio.TYPES.get(item["kind"], {}).get("format") != "html" or not item["content"]:
        abort(404)
    # ?v=<n> shows an older version (0-based) while browsing history in the viewer.
    versions, v = item.get("versions") or [], request.args.get("v", type=int)
    content = versions[v]["content"] if v is not None and 0 <= v < len(versions) else None
    return Response(studio.page_html(item, content), mimetype="text/html",
                    headers={"Content-Security-Policy": ARTIFACT_CSP, "X-Content-Type-Options": "nosniff"})


@app.get("/api/studio/<item_id>/audio")
def studio_audio(item_id):
    """The podcast MP3 for the player (?v=<n> = an older version)."""
    item = need(db.one("SELECT * FROM studio_items WHERE id = ?", (item_id,)))
    versions, v = item.get("versions") or [], request.args.get("v", type=int)
    content = versions[v]["content"] if v is not None and 0 <= v < len(versions) else item["content"]
    if not studio.is_podcast(item, content):
        abort(404)
    return send_file(need(studio.podcast_path(item_id, content)), mimetype="audio/mpeg", conditional=True)


@app.get("/api/studio/<item_id>/download")
def download_studio_item(item_id):
    item = need(db.one("SELECT * FROM studio_items WHERE id = ?", (item_id,)))
    if studio.is_image(item):
        path = need(studio.image_path(item_id, item["content"]))
        return send_file(path, mimetype="image/png", as_attachment=True,
                         download_name=skills.slugify(item["title"]) + ".png")
    if studio.is_podcast(item):
        path = need(studio.podcast_path(item_id, item["content"]))
        return send_file(path, mimetype="audio/mpeg", as_attachment=True,
                         download_name=skills.slugify(item["title"]) + ".mp3")
    fmt = "note" if item["kind"] == "note" else studio.TYPES[item["kind"]]["format"]
    ext, mime = {"html": ("html", "text/html"), "json": ("json", "application/json")}.get(fmt, ("md", "text/markdown"))
    name = skills.slugify(item["title"]) + "." + ext
    content = studio.page_html(item) if fmt == "html" else item["content"] or ""
    return Response(content, mimetype=f"{mime}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- search + export ----------
@app.get("/api/search")
def search_all():
    return jsonify(db.search(request.args.get("q", "")[:200]))


@app.get("/api/notebooks/<nb_id>/export")
def export_notebook(nb_id):
    """A zip anyone can open: README, sources as Markdown, the chat, and every Studio output (PNG for AI images)."""
    import io
    import zipfile
    nb = need(db.one("SELECT * FROM notebooks WHERE id = ?", (nb_id,)))
    buf = io.BytesIO()
    lines = [f"# {nb['title']}", "", nb.get("summary") or "", "", f"Exported from Cueline on {db.now()[:10]}.", "", "## Sources"]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, src in enumerate(db.sources(nb_id, with_content=True), 1):
            if src["kind"] == "notebook":
                lines.append(f"- {src['title']} (linked notebook, not included)")
                continue
            name = f"sources/{n:02d}-{skills.slugify(src['title'])[:60]}.md"
            z.writestr(name, f"# {src['title']}\n\n" + (f"<{src['url']}>\n\n" if src.get("url") else "") + (src["content"] or ""))
            lines.append(f"- [{src['title']}]({name})" + (f" ({src['url']})" if src.get("url") else ""))
        msgs = db.messages(nb_id)
        if msgs:
            z.writestr("chat.md", f"# Chat: {nb['title']}\n\n" + "\n\n".join(
                f"**{'You' if m['role'] == 'user' else 'Cueline'}** ({m['created_at'][:16].replace('T', ' ')})\n\n{m['content']}" for m in msgs))
            lines += ["", "## Chat", "- [chat.md](chat.md)"]
        studio_lines = []
        for item in db.query("SELECT * FROM studio_items WHERE notebook_id = ? AND status = 'done' ORDER BY created_at", (nb_id,)):
            base = f"studio/{skills.slugify(item['title'])[:60]}-{item['id'][:6]}"
            if studio.is_image(item) or studio.is_podcast(item):
                image = studio.is_image(item)
                path = (studio.image_path if image else studio.podcast_path)(item["id"], item["content"])
                if not path:
                    continue
                name = base + (".png" if image else ".mp3")
                z.write(path, name)
                if not image:  # the script + fact check next to the audio
                    meta = json.loads(item["content"])
                    z.writestr(base + "-script.md", f"# {meta['title']}\n\n{meta.get('summary', '')}\n\n" + "\n\n".join(
                        f"**{meta['hosts'].get(t['speaker'], t['speaker'])}:** {t['text']}" for t in meta["turns"])
                        + "\n\n## Fact check\n" + "\n".join(f"- {f['claim']} {f['cite']}" for f in meta["fact_check"]))
            else:
                fmt = "note" if item["kind"] == "note" else studio.TYPES.get(item["kind"], {}).get("format", "markdown")
                name = base + {"html": ".html", "json": ".json"}.get(fmt, ".md")
                z.writestr(name, studio.page_html(item) if fmt == "html" else item["content"] or "")
            studio_lines.append(f"- [{item['title']}]({name})")
        if studio_lines:
            lines += ["", "## Studio outputs", *studio_lines]
        z.writestr("README.md", "\n".join(lines) + "\n")
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{skills.slugify(nb["title"])}-cueline.zip"'})


# ---------- projects ----------
BRAND_KIT_TEMPLATE = """---
name: {slug}-brand-kit
description: Write as {name}: voice, audience, offers and rules. Use for any content made for {name}.
---

# {name} brand kit

## Who they are
{description}

## Audience
- **Who:**
- **Pains:**
- **Desires:**
- **Objections:**

## Voice & tone
- **<trait>**: do ... / don't ...

## Vocabulary
- **Say:**
- **Avoid:**

## Offers
- **<offer>**: <promise>, CTA: ...

## Content pillars
1.

## Hooks & formats that fit
-

## Proof we can use
-

## Rules
-

## Examples
-
"""


def _project_fields(body: dict) -> dict:
    fields = {}
    if "name" in body:
        name = str(body.get("name") or "").strip()[:80]
        if not name:
            raise ValueError("Give the project a name.")
        fields["name"] = name
    if "description" in body:
        fields["description"] = str(body.get("description") or "").strip()[:600]
    if body.get("emoji"):
        fields["emoji"] = str(body["emoji"]).strip()[:8]
    if "brand_skill_id" in body:
        if body["brand_skill_id"]:
            need(db.one("SELECT id FROM skills WHERE id = ?", (body["brand_skill_id"],)))
        fields["brand_skill_id"] = body["brand_skill_id"] or None
    return fields


@app.get("/api/projects")
def list_projects():
    return jsonify(db.projects())


@app.post("/api/projects")
def create_project():
    body = request.get_json(silent=True) or {}
    try:
        fields = _project_fields({"name": body.get("name"), **body})
    except ValueError as exc:
        return err(str(exc))
    now = db.now()
    fields.setdefault("emoji", "📁")
    return jsonify(db.insert("projects", **fields, created_at=now, updated_at=now)), 201


@app.get("/api/projects/<project_id>")
def get_project(project_id):
    project = need(db.one("SELECT * FROM projects WHERE id = ?", (project_id,)))
    project["notebooks"] = db.notebooks(project_id)
    project["brand_kit"] = db.one("SELECT id, name, description, version, installed_path, updated_at FROM skills WHERE id = ?",
                                  (project["brand_skill_id"],)) if project["brand_skill_id"] else None
    return jsonify(project)


@app.patch("/api/projects/<project_id>")
def update_project(project_id):
    need(db.one("SELECT id FROM projects WHERE id = ?", (project_id,)))
    try:
        fields = _project_fields(request.get_json(silent=True) or {})
    except ValueError as exc:
        return err(str(exc))
    return jsonify(db.update("projects", project_id, **fields, updated_at=db.now()))


@app.delete("/api/projects/<project_id>")
def delete_project(project_id):
    with db.connect() as conn:  # a project is only a folder: its notebooks and brand kit stay
        conn.execute("UPDATE notebooks SET project_id = NULL WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return "", 204


@app.post("/api/projects/<project_id>/brand-kit")
def brand_kit_template(project_id):
    project = need(db.one("SELECT * FROM projects WHERE id = ?", (project_id,)))
    body = BRAND_KIT_TEMPLATE.format(slug=skills.slugify(project["name"]), name=project["name"],
                                     description=project["description"] or "<2-3 sentences: what they do, for whom>")
    skill = skills.create_from_markdown(body, origin="manual", origin_ref=f"project:{project_id}")
    db.update("projects", project_id, brand_skill_id=skill["id"], updated_at=db.now())
    return jsonify(skill), 201


# ---------- live sources (follows) + their inbox (updates) ----------
def _resolve_follow(body: dict) -> dict:
    if body.get("query"):
        return feeds.resolve_search(str(body["query"]))
    return feeds.resolve(str(body.get("url") or ""))


@app.post("/api/follows/preview")
def preview_follow():
    """Find what a link (or YouTube search) points to and show its newest items. Stores nothing."""
    try:
        info = _resolve_follow(request.get_json(silent=True) or {})
        return jsonify({**info, "latest": feeds.preview(info)})
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"Couldn't read that: {exc}", 502)


@app.post("/api/notebooks/<nb_id>/follows")
def add_follow(nb_id):
    need(db.one("SELECT id FROM notebooks WHERE id = ?", (nb_id,)))
    try:
        info = _resolve_follow(request.get_json(silent=True) or {})
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"Couldn't read that: {exc}", 502)
    if db.one("SELECT id FROM follows WHERE notebook_id = ? AND feed_url = ?", (nb_id, info["feed_url"])):
        return err("This notebook already follows that.", 409)
    body = request.get_json(silent=True) or {}
    f = db.insert("follows", notebook_id=nb_id, enabled=1, auto_add=1 if body.get("auto_add") else 0,
                  created_at=db.now(), **info)
    db.touch_notebook(nb_id)
    threading.Thread(target=watch.check_follow, args=(f["id"],), daemon=True).start()
    return jsonify(f), 201


@app.patch("/api/follows/<follow_id>")
def update_follow(follow_id):
    need(db.one("SELECT id FROM follows WHERE id = ?", (follow_id,)))
    body = request.get_json(silent=True) or {}
    fields = {}
    if "enabled" in body:
        fields["enabled"] = 1 if body["enabled"] else 0
        if body["enabled"]:  # resuming gives a paused or failing source a fresh start
            fields.update(fail_count=0, last_error=None)
    if "auto_add" in body:
        fields["auto_add"] = 1 if body["auto_add"] else 0
    if body.get("title"):
        fields["title"] = str(body["title"]).strip()[:200]
    return jsonify(db.update("follows", follow_id, **fields))


@app.delete("/api/follows/<follow_id>")
def delete_follow(follow_id):
    db.delete("follows", follow_id)  # its inbox goes too; sources already added stay
    return "", 204


@app.post("/api/follows/<follow_id>/check")
def check_follow_now(follow_id):
    need(db.one("SELECT id FROM follows WHERE id = ?", (follow_id,)))
    return jsonify(watch.check_follow(follow_id))


UPDATE_COLS = """u.id, u.follow_id, u.notebook_id, u.kind, u.title, u.url, u.author, u.published_at, u.meta, u.status,
                 u.triage, u.error, u.state, u.source_id, u.created_at, u.updated_at,
                 f.title AS follow_title, f.kind AS follow_kind, n.title AS notebook_title, n.emoji AS notebook_emoji,
                 p.id AS project_id, p.name AS project_name, p.emoji AS project_emoji"""


@app.get("/api/updates")
def list_updates():
    a = request.args
    sql = f"""SELECT {UPDATE_COLS} FROM updates u JOIN follows f ON f.id = u.follow_id
              JOIN notebooks n ON n.id = u.notebook_id LEFT JOIN projects p ON p.id = n.project_id
              WHERE u.status != 'skipped'"""
    params: list = []
    if a.get("state", "inbox") in ("inbox", "added", "dismissed"):
        sql += " AND u.state = ?"
        params.append(a.get("state", "inbox"))
    for arg, col in (("project", "n.project_id"), ("notebook", "u.notebook_id"), ("follow", "u.follow_id")):
        if a.get(arg):
            sql += f" AND {col} = ?"
            params.append(a[arg])
    # Newest finds first, in the source's own order within one check. Not by publish date: reading a video fills in
    # its upload date (often years old), which made items jump down the list and look like they had disappeared.
    sql += " ORDER BY u.created_at DESC, u.rowid ASC LIMIT 200"
    return jsonify({"items": db.query(sql, params), "status": watch.status()})


@app.post("/api/updates/check")
def check_updates():
    body = request.get_json(silent=True) or {}
    if not db.one("SELECT id FROM follows WHERE enabled = 1 LIMIT 1"):
        return err("Follow a channel, feed or search in a notebook first.")
    watch.check_async(force=True, notebook_id=body.get("notebook_id") or None, project_id=body.get("project_id") or None)
    return jsonify(watch.status()), 202


@app.patch("/api/updates/<update_id>")
def update_update(update_id):
    need(db.one("SELECT id FROM updates WHERE id = ?", (update_id,)))
    state = (request.get_json(silent=True) or {}).get("state")
    if state not in ("inbox", "dismissed"):
        return err("Unknown state")
    return jsonify(db.update("updates", update_id, state=state, updated_at=db.now()))


@app.post("/api/updates/<update_id>/retry")
def retry_update(update_id):
    need(db.one("SELECT id FROM updates WHERE id = ?", (update_id,)))
    watch.retry(update_id)
    return "", 202


@app.post("/api/updates/<update_id>/add")
def add_update(update_id):
    """Turn an inbox item into a source of its notebook (reads the full text first if AI reading was off)."""
    need(db.one("SELECT id FROM updates WHERE id = ?", (update_id,)))
    try:
        result = watch.add_to_notebook(update_id)
    except Exception as exc:
        return err(f"Couldn't read this item: {exc}", 502)
    return jsonify(result), 200 if result["existing"] else 201


@app.get("/api/settings")
def get_settings():
    return jsonify({"updates_ai": watch.ai_mode(), "daily_cap": watch.DAILY_CAP, "interval_hours": watch.INTERVAL_HOURS})


@app.put("/api/settings")
def save_settings():
    body = request.get_json(silent=True) or {}
    if "updates_ai" in body:
        if body["updates_ai"] not in watch.AI_MODES:
            return err("Unknown AI mode")
        db.set_setting("updates_ai", body["updates_ai"])
    return get_settings()


# ---------- AI provider + model (keys never leave the server: the browser only sees has_key + last 4) ----------
@app.get("/api/ai")
def ai_settings():
    provider, model = engine.choice()
    rows = []
    for pid, p in llm.PROVIDERS.items():
        key = llm.get_key(pid) if p["kind"] == "key" else ""
        rows.append({"id": pid, "label": p["label"], "kind": p["kind"], "note": p.get("note", ""),
                     "keys_url": p.get("keys_url", ""), "has_key": bool(key), "key_hint": key[-4:] if key else "",
                     "model": db.setting(f"ai_model_{pid}", "") or p.get("default", "")})
    return jsonify({"provider": provider, "model": model, "label": llm.PROVIDERS[provider]["label"],
                    "providers": rows, "status": engine.status()})


@app.put("/api/ai")
def save_ai_settings():
    body = request.get_json(silent=True) or {}
    provider = body.get("provider")
    if provider not in llm.PROVIDERS:
        return err("Unknown AI provider")
    if llm.PROVIDERS[provider]["kind"] == "key" and not llm.get_key(provider):
        return err(f"Add your {llm.PROVIDERS[provider]['label']} API key first.")
    db.set_setting(f"ai_model_{provider}", str(body.get("model") or "").strip())
    db.set_setting("ai_provider", provider)
    if provider == "codex":
        threading.Thread(target=engine.warm_up, daemon=True).start()
    return ai_settings()


@app.get("/api/ai/models")
def ai_models():
    provider = request.args.get("provider", "")
    if provider not in llm.PROVIDERS:
        return err("Unknown AI provider")
    try:
        return jsonify({"models": engine.list_models(provider)})
    except Exception as exc:
        return err(str(exc) or "Could not list models", 502)


@app.put("/api/ai/keys/<provider>")
def save_ai_key(provider):
    if llm.PROVIDERS.get(provider, {}).get("kind") != "key":
        return err("This provider doesn't use an API key")
    key = str((request.get_json(silent=True) or {}).get("key") or "").strip()
    if not key or len(key) > 400 or any(c.isspace() for c in key):
        return err("Paste the whole key (no spaces).")
    try:  # check it before saving: listing models is free on every provider
        models = engine.list_models(provider, key=key)
    except Exception as exc:
        return err(str(exc) or "That key didn't work.", 400)
    llm.set_key(provider, key)
    return jsonify({"ok": True, "models": models})


@app.delete("/api/ai/keys/<provider>")
def delete_ai_key(provider):
    if llm.PROVIDERS.get(provider, {}).get("kind") != "key":
        return err("This provider doesn't use an API key")
    llm.set_key(provider, None)
    if engine.choice()[0] == provider:
        db.set_setting("ai_provider", "codex")
    return ai_settings()


# ---------- image engine (AI-image infographics) ----------
@app.get("/api/ai/images")
def image_settings():
    provider, model = images.choice()
    rows = [{"id": pid, "label": p["label"], "note": p["note"], "model": db.setting(f"image_model_{pid}", "") or p.get("default", ""),
             "ready": pid == "codex" or bool(llm.get_key(pid)), "has_model": "default" in p} for pid, p in images.PROVIDERS.items()]
    return jsonify({"provider": provider, "model": model, "providers": rows})


@app.put("/api/ai/images")
def save_image_settings():
    body = request.get_json(silent=True) or {}
    provider = body.get("provider")
    if provider not in images.PROVIDERS:
        return err("Unknown image engine")
    if provider != "codex" and not llm.get_key(provider):
        return err(f"Add your {images.PROVIDERS[provider]['label']} API key first.")
    if "model" in body:
        db.set_setting(f"image_model_{provider}", str(body.get("model") or "").strip()[:100])
    db.set_setting("image_provider", provider)
    return image_settings()


# ---------- podcast voices ----------
@app.get("/api/ai/voices")
def voice_settings():
    provider, model = tts.choice()
    rows = [{"id": pid, "label": p["label"], "note": p["note"], "model": db.setting(f"tts_model_{pid}", "") or p.get("default", ""),
             "ready": pid == "kokoro" or bool(llm.get_key(pid)), "has_model": "default" in p} for pid, p in tts.PROVIDERS.items()]
    return jsonify({"provider": provider, "model": model, "providers": rows})


@app.put("/api/ai/voices")
def save_voice_settings():
    body = request.get_json(silent=True) or {}
    provider = body.get("provider")
    if provider not in tts.PROVIDERS:
        return err("Unknown voice engine")
    if provider != "kokoro" and not llm.get_key(provider):
        return err(f"Add your {tts.PROVIDERS[provider]['label']} API key first.")
    if "model" in body:
        db.set_setting(f"tts_model_{provider}", str(body.get("model") or "").strip()[:100])
    db.set_setting("tts_provider", provider)
    return voice_settings()


# ---------- skills ----------
@app.get("/api/skills")
def list_skills():
    return jsonify(db.skills())


@app.post("/api/skills")
def create_skill():
    body = (request.get_json(silent=True) or {}).get("body") or (
        "---\nname: new-skill\ndescription: What this skill does and when to use it.\n---\n\n# New skill\n\n"
        "## Purpose\n\n## When to use\n- \n\n## Steps\n1. \n\n## Pitfalls\n- \n\n## Checks\n- [ ] \n")
    return jsonify(skills.create_from_markdown(body, origin="manual")), 201


@app.get("/api/skills/importable")
def importable_skills():
    return jsonify(skills.importable())


@app.post("/api/skills/import")
def import_skills():
    paths = (request.get_json(silent=True) or {}).get("paths") or []
    try:
        return jsonify(skills.import_paths(paths)), 201
    except ValueError as exc:
        return err(str(exc))


@app.get("/api/skills/<skill_id>")
def get_skill(skill_id):
    return jsonify(need(db.one("SELECT * FROM skills WHERE id = ?", (skill_id,))))


@app.put("/api/skills/<skill_id>")
def save_skill(skill_id):
    need(db.one("SELECT id FROM skills WHERE id = ?", (skill_id,)))
    body = (request.get_json(silent=True) or {}).get("body") or ""
    if not body.strip():
        return err("A skill can't be empty.")
    return jsonify(skills.save(skill_id, body))


@app.delete("/api/skills/<skill_id>")
def delete_skill(skill_id):
    db.delete("skills", skill_id)
    return "", 204


@app.get("/api/skills/<skill_id>/install")
def install_preview(skill_id):
    skill = need(db.one("SELECT * FROM skills WHERE id = ?", (skill_id,)))
    try:
        return jsonify(skills.install_preview(skill))
    except ValueError as exc:
        return err(str(exc))


@app.post("/api/skills/<skill_id>/install")
def install_skill(skill_id):
    skill = need(db.one("SELECT * FROM skills WHERE id = ?", (skill_id,)))
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return err("Install needs explicit confirmation.")
    try:
        links = body.get("links") if isinstance(body.get("links"), list) else []
        return jsonify(skills.install(skill, links=[str(x) for x in links]))
    except (ValueError, OSError) as exc:
        return err(str(exc))


@app.get("/api/skills/<skill_id>/download")
def download_skill(skill_id):
    skill = need(db.one("SELECT * FROM skills WHERE id = ?", (skill_id,)))
    return Response(skill["body"], mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{skill["name"]}.SKILL.md"'})


def startup():
    db.init()
    with db.connect() as conn:  # generations don't survive a restart
        conn.execute("UPDATE studio_items SET status = 'error', error = 'Interrupted by a restart. Try again.' "
                     "WHERE status IN ('queued', 'running')")
    retrieval.prune()
    try:
        db.backup_if_due()
    except Exception as exc:  # never block startup on a backup
        print(f"Backup failed: {exc}")
    threading.Thread(target=engine.warm_up, daemon=True).start()
    watch.start()
    stt.start()


def serve(port: int = 8790):
    import logging
    from flask import cli
    # Werkzeug's "development server" warning targets public deployments; Cueline only listens on 127.0.0.1.
    # This also drops one log line per request (the UI polls every 8 s). Our own warnings still show.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    cli.show_server_banner = lambda *args, **kwargs: None
    startup()
    print(f"Cueline running: http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
