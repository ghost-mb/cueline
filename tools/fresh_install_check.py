"""Fresh-install check: install Cueline the way a new user would and run the main flows against a fake AI.

    python3 tools/fresh_install_check.py            # needs uv on PATH and git; no AI quota or keys used

Copies only the files git would publish into a temp folder, installs them with `uv tool install` (as the README
says; core only, no audio extra) under an empty HOME with a PATH without Codex, runs `cueline`, points the OpenAI
provider at a local fake server, then checks: web files served, first-run AI state, key setup, notebook + text
source, streamed chat, Studio outputs, skill install into Claude Code, the podcast audio-extra message, localhost
guard, export. Optional: .private-words (one word per line, gitignored) fails the check if any published file
contains one of them. Exit code 0 = every check passed.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- fake OpenAI API: streams canned text, or the smallest object that fits a JSON schema ----------
def fill(s):
    t = s.get("type")
    if isinstance(t, list):
        t = next(x for x in t if x != "null")
    if "enum" in s:
        return s["enum"][0]
    if t == "object":
        return {k: fill(v) for k, v in s.get("properties", {}).items()}
    if t == "array":
        return [fill(s.get("items", {})) for _ in range(max(1, s.get("minItems", 1)))]
    if t in ("integer", "number"):
        return s.get("minimum", 1)
    if t == "boolean":
        return True
    return "Feed the starter twice a day [1]."


SKILL = ("---\nname: sourdough-feeding\ndescription: Feed a sourdough starter.\n---\n# Sourdough\n"
         "Feed the starter twice a day with equal flour and water [1].")


class FakeAI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # model list = key check
        body = json.dumps({"object": "list", "data": [{"id": "gpt-fake", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        rf = req.get("response_format") or {}
        out = json.dumps(fill(rf["json_schema"]["schema"])) if rf.get("type") == "json_schema" else SKILL
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "gpt-fake",
                 "choices": [{"index": 0, "delta": {"content": out}, "finish_reason": None}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        chunk["choices"][0] = {"index": 0, "delta": {}, "finish_reason": "stop"}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())


# ---------- Cueline API client ----------
def call(base, method, path, body=None, host=None, raw=False):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Cueline": "1", "Content-Type": "application/json", **({"Host": host} if host else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            return r.status, data if raw else json.loads(data or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def wait_studio(base, item_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        _, item = call(base, "GET", f"/api/studio/{item_id}")
        if item["status"] in ("done", "error"):
            return item
        time.sleep(1)
    return item


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to a legacy code page
    uv = shutil.which("uv")
    if not uv:
        sys.exit("Needs uv on PATH.")
    work = Path(tempfile.mkdtemp(prefix="cueline-fresh-"))
    app_dir, home, bin_dir = work / "cueline", work / "home", work / "bin"
    for d in (home, bin_dir):
        d.mkdir()
    shutil.copy(uv, bin_dir / Path(uv).name)  # a copy: symlinks need extra rights on Windows
    exe = ".exe" if os.name == "nt" else ""

    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    for f in files:
        (app_dir / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, app_dir / f)
    print(f"Published files: {len(files)} → {app_dir}")

    words_file = ROOT / ".private-words"
    words = [w.strip().lower() for w in words_file.read_text().splitlines() if w.strip()] if words_file.exists() else []
    leaks = [f"{f}: {w}" for f in files for w in words
             if w in (ROOT / f).read_text(encoding="utf-8", errors="ignore").lower()]
    check(not leaks, f"no private words in published files ({len(words)} checked)" + (f": {leaks[:5]}" if leaks else ""))

    ai_port, port = free_port(), free_port()
    fake = ThreadingHTTPServer(("127.0.0.1", ai_port), FakeAI)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    system = [os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"] if os.name == "nt" else ["/usr/bin", "/bin"]
    env = {"HOME": str(home), "USERPROFILE": str(home), "PATH": os.pathsep.join([str(bin_dir), *system]),
           **{k: os.environ[k] for k in ("SystemRoot", "TEMP", "TMP") if k in os.environ},
           "OPENAI_BASE_URL": f"http://127.0.0.1:{ai_port}/v1",
           "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv"))}
    env.update(UV_TOOL_DIR=str(work / "tools"), UV_TOOL_BIN_DIR=str(bin_dir), UV_PYTHON_INSTALL_DIR=str(work / "py"))
    log = open(work / "server.log", "w")
    started = time.time()
    inst = subprocess.run([uv, "tool", "install", "-q", str(app_dir)], env=env, capture_output=True, text=True)
    check(inst.returncode == 0 and (bin_dir / f"cueline{exe}").exists(), "uv tool install gives a `cueline` command"
          + (f": {inst.stderr[-300:]}" if inst.returncode else ""))
    if inst.returncode:
        return
    server = subprocess.Popen([str(bin_dir / f"cueline{exe}"), "--port", str(port)], cwd=home, env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(1200):
            try:
                urllib.request.urlopen(base + "/api/status", timeout=1)
                break
            except OSError:
                if server.poll() is not None:
                    break
                time.sleep(0.5)
        size = sum(p.stat().st_size for p in (work / "tools").rglob("*") if p.is_file()) // 2**20
        print(f"Install + start: {time.time() - started:.0f} s, install {size} MB")
        check(server.poll() is None, "`cueline` starts the server")
        if server.poll() is not None:
            return
        check(b"<html" in call(base, "GET", "/", raw=True)[1].lower()
              and call(base, "GET", "/static/js/main.js", raw=True)[0] == 200, "web files are in the package")
        check((home / ".cueline" / "cueline.db").exists(), "data goes to ~/.cueline")

        _, s = call(base, "GET", "/api/status")
        check(s["ai"]["state"] == "error" and s["ai"]["provider"] == "codex", "no Codex → first-run 'Pick your AI'")
        check(s["hermes"] is False, "Hermes features hidden")
        check(call(base, "GET", "/api/status", host="evil.example")[0] == 403, "other host names refused")

        check(call(base, "PUT", "/api/ai/keys/openai", {"key": "sk-fake"})[0] == 200, "API key saved")
        check(call(base, "PUT", "/api/ai", {"provider": "openai", "model": "gpt-fake"})[0] == 200, "provider switched")

        _, nb = call(base, "POST", "/api/notebooks", {"title": "Fresh"})
        code, _ = call(base, "POST", f"/api/notebooks/{nb['id']}/sources",
                       {"type": "text", "title": "Starter", "text": "Feed the sourdough starter twice a day."})
        check(code in (200, 201), "text source added")

        _, chat = call(base, "POST", f"/api/notebooks/{nb['id']}/chat", {"message": "How often?"}, raw=True)
        check(b"event: done" in chat, "chat streams an answer")

        for kind in ("quiz", "content", "skill"):
            _, item = call(base, "POST", f"/api/notebooks/{nb['id']}/studio", {"kind": kind})
            item = wait_studio(base, item["id"])
            check(item["status"] == "done", f"Studio {kind} finishes ({item.get('error') or 'ok'})")

        code, r = call(base, "POST", f"/api/notebooks/{nb['id']}/studio", {"kind": "podcast"})
        check(code == 400 and "cueline[audio]" in r.get("error", ""), "podcast without audio extra explains cueline[audio]")

        _, sk = call(base, "GET", "/api/skills")
        code, r = call(base, "GET", f"/api/skills/{sk[0]['id']}/install")
        check(code == 400 and "No AI agent" in r.get("error", ""), "no agent installed → clear install message")
        (home / ".claude").mkdir()  # now "install" Claude Code: a fresh ~/.claude has no skills/ folder
        code, r = call(base, "POST", f"/api/skills/{sk[0]['id']}/install", {"confirm": True, "links": []})
        check(code == 200 and (home / ".claude/skills" / sk[0]["name"] / "SKILL.md").is_file(),
              "skill installs into Claude Code")

        check(call(base, "GET", f"/api/notebooks/{nb['id']}/export", raw=True)[0] == 200, "export zip")
    finally:
        server.terminate()
        server.wait(10)
        fake.shutdown()
        if FAILS:
            print(f"Server log: {work / 'server.log'}")
        else:
            shutil.rmtree(work, ignore_errors=True)  # ~100 MB install; /tmp is often in RAM
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
