"""The `cueline` command: start the app, or talk to a running one from the terminal (handy for AI agents).

  cueline [--port 8790]                              start Cueline → http://127.0.0.1:8790
  cueline --version                                  print the installed version
  cueline notebooks                                  list notebooks (id, sources, title)
  cueline new "Title"                                create a notebook, print its id
  cueline add <notebook> <youtube-url>               add a YouTube video (captions)
  cueline add <notebook> <web-url>                   add a blog post or web page (main text)
  cueline add <notebook> <file>                      upload PDF/DOCX/TXT/MD, or audio (transcribed on this computer)
  cueline add <notebook> --text "..." [--title T]    add pasted text
  cueline ask <notebook> "question"                  ask the notebook; prints the cited answer (saved in its chat)

<notebook> = id, or part of the title (must match exactly one). The other commands need Cueline running.
CUELINE_PORT sets the port for all of these, if you don't use the default 8790.
"""
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PORT = os.environ.get("CUELINE_PORT", "8790")
URL = f"http://127.0.0.1:{PORT}"


def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def call(method: str, path: str, body=None, raw: bytes | None = None, ctype: str = "application/json", timeout=120):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"X-Cueline": "1", **({"Content-Type": ctype} if data is not None else {})})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("error") or exc.reason
        except Exception:
            msg = exc.reason
        die(f"Cueline: {msg}")
    except urllib.error.URLError:
        die("Cueline is not running. Start it with: cueline")


def get(path):
    return json.loads(call("GET", path).read())


def find_notebook(ref: str) -> dict:
    books = get("/api/notebooks")
    exact = [b for b in books if b["id"] == ref or b["title"].lower() == ref.lower()]
    hits = exact or [b for b in books if ref.lower() in b["title"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        die(f"No notebook matches {ref!r}. Run: cueline notebooks")
    die(f"{ref!r} matches {len(hits)} notebooks: " + "; ".join(f"{b['id']} {b['title']}" for b in hits[:8]))


def cmd_notebooks(_args):
    for b in get("/api/notebooks"):
        print(f"{b['id']}  {b.get('source_count', '?'):>3} sources  {b['title']}")


def cmd_new(args):
    if not args:
        die('Usage: cueline new "Title"')
    nb = json.loads(call("POST", "/api/notebooks", {"title": " ".join(args)}).read())
    print(f"Created notebook {nb['id']}: {nb['title']}")


def cmd_add(args):
    if len(args) < 2:
        die("Usage: cueline add <notebook> <youtube-url | file | --text \"...\" [--title T]>")
    nb = find_notebook(args[0])
    rest = args[1:]
    if rest[0] == "--text":
        title = rest[rest.index("--title") + 1] if "--title" in rest else ""
        text = rest[1] if len(rest) > 1 else ""
        src = json.loads(call("POST", f"/api/notebooks/{nb['id']}/sources", {"type": "text", "title": title, "text": text}).read())
    elif rest[0].startswith(("http://", "https://")):
        kind = "youtube" if "youtu" in rest[0] else "web"  # web: blog post or page (sites may block reading)
        src = json.loads(call("POST", f"/api/notebooks/{nb['id']}/sources", {"type": kind, "url": rest[0]}, timeout=300).read())
    else:
        path = Path(rest[0]).expanduser()
        if not path.is_file():
            die(f"No such file: {path}")
        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
               f"Content-Type: {mime}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        src = json.loads(call("POST", f"/api/notebooks/{nb['id']}/upload", raw=raw,
                              ctype=f"multipart/form-data; boundary={boundary}", timeout=300).read())
    note = " Transcribing on this computer; it becomes searchable when done." if (src.get("meta") or {}).get("stt") else ""
    print(f"Added to {nb['title']!r}: {src['title']} ({src['kind']}).{note}")


def cmd_ask(args):
    if len(args) < 2:
        die('Usage: cueline ask <notebook> "question"')
    nb = find_notebook(args[0])
    res = call("POST", f"/api/notebooks/{nb['id']}/chat", {"message": " ".join(args[1:])}, timeout=600)
    event = None
    for line in res:
        line = line.decode().rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event in ("done", "error"):
            data = json.loads(line[6:])
            if event == "error":
                die(f"Cueline: {data}")
            print(data["content"])
            bad = ((data.get("checks") or {}).get("bad") or [])
            if bad:
                print(f"\n(Cueline could not verify {len(bad)} citation(s) in this answer.)")
            print(f"\n— from notebook {nb['title']!r} ({URL}/#/n/{nb['id']})")
            return
    die("Cueline closed the answer stream early.")


COMMANDS = {"notebooks": cmd_notebooks, "new": cmd_new, "add": cmd_add, "ask": cmd_ask}

def main():
    sys.stdout.reconfigure(errors="replace")  # titles and answers may hold characters a Windows console can't show
    args = sys.argv[1:]
    if args and args[0] in COMMANDS:
        return COMMANDS[args[0]](args[1:])
    if args in (["-V"], ["--version"]):
        from importlib.metadata import version
        return print("cueline", version("cueline"))
    args = [part for arg in args for part in arg.split("=", 1)]  # --port=9000 works too
    if not args or (len(args) == 2 and args[0] == "--port" and args[1].isdigit()):
        from .app import serve  # imported here so agent commands start instantly
        return serve(int(args[1] if args else PORT))
    print(__doc__.strip())
    sys.exit(0 if args in (["-h"], ["--help"]) else 2)


if __name__ == "__main__":
    main()
