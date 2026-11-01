"""AI engine. `engine` routes every call to the provider chosen in Settings (top bar → AI model):

- "codex" (connector, default): one persistent, locked-down `codex app-server` process (JSON-RPC over stdio) on the
  user's ChatGPT login (same subscription Hermes uses). Every call runs in a fresh ephemeral thread whose base
  instructions are ours, so Codex's own coding-agent prompt is replaced. All tools are off: the model can only write.
- "anthropic" / "openai" / "gemini" / "openrouter" (API key): see llm_api.py. Keys live in data/keys.json (0600)
  or CUELINE_<PROVIDER>_API_KEY, are never sent to the browser, and are never logged.

    text = engine.generate(system, prompt, on_delta=print, schema=None, effort="low")
"""
import json
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db

CODEX_BIN = os.environ.get("CUELINE_CODEX_BIN", "codex")
WORKDIR = db.DATA_DIR / "codex-cwd"  # empty dir: nothing to read even if a tool slipped through
MAX_PARALLEL = 3

# Everything that lets the model act instead of write. Verified with a forced file-read test (see docs/PLAN.md).
DISABLED_FEATURES = [
    "shell_tool", "unified_exec", "shell_snapshot", "code_mode_host", "apps", "plugins", "remote_plugin",
    "browser_use", "browser_use_external", "computer_use", "in_app_browser", "sleep_tool", "tool_suggest",
    "image_generation", "view_image", "goals", "multi_agent",
]


class LLMError(RuntimeError):
    pass


class QuotaError(LLMError):
    """The provider said rate limit / usage limit. retry_after: seconds it asked us to wait (None = unknown)."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


QUOTA_RE = re.compile(r"usage limit|rate limit|rate_limit|quota|too many requests|\b429\b", re.I)


def _codex_error(message: str) -> LLMError:
    return QuotaError(message) if QUOTA_RE.search(message or "") else LLMError(message)


def parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise LLMError("The AI returned malformed data. Try again.")


def _mcp_server_names() -> list[str]:
    cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted(set(re.findall(r"^\[mcp_servers\.([A-Za-z0-9_-]+)\]", text, re.M)))


class CodexEngine:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()          # guards process start + request ids
        self._wlock = threading.Lock()         # guards stdin writes (reader thread writes too)
        self._pending: dict[int, queue.Queue] = {}
        self._threads: dict[str, queue.Queue] = {}  # threadId -> notification queue
        self._next_id = 0
        self._slots = threading.Semaphore(MAX_PARALLEL)
        self.model = None
        self.chosen = None  # model picked in Settings; None = Codex's default
        self.state = "idle"  # idle | starting | ready | error
        self.last_error = ""

    # ---------- process ----------
    def _command(self) -> list[str]:
        cmd = [CODEX_BIN, "app-server"]
        for feature in DISABLED_FEATURES:
            cmd += ["-c", f"features.{feature}=false"]
        cmd += ["-c", 'web_search="disabled"']
        for name in _mcp_server_names():
            cmd += ["-c", f"mcp_servers.{name}.enabled=false"]
        return cmd

    def _ensure_started(self):
        if self._proc and self._proc.poll() is None:
            return
        self.state = "starting"
        WORKDIR.mkdir(parents=True, exist_ok=True)
        try:
            self._proc = subprocess.Popen(
                self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, cwd=WORKDIR,
            )
        except FileNotFoundError as exc:
            self.state, self.last_error = "error", "Codex CLI not found. Is `codex` installed and on PATH?"
            raise LLMError(self.last_error) from exc
        self._pending.clear()
        self._threads.clear()
        threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
        self._request_locked("initialize", {
            "clientInfo": {"name": "cueline", "title": "Cueline", "version": "1.0"}, "capabilities": {}})
        self._write({"jsonrpc": "2.0", "method": "initialized"})

    def _reader(self, proc):
        for line in proc.stdout:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" in msg and "id" in msg:  # server -> client request (approvals etc.): always refuse
                self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"decision": "decline"}})
            elif "id" in msg:
                q = self._pending.pop(msg["id"], None)
                if q:
                    q.put(msg)
            else:
                thread_id = (msg.get("params") or {}).get("threadId")
                q = self._threads.get(thread_id)
                if q:
                    q.put(msg)
        # process died: wake everyone up
        for q in list(self._pending.values()) + list(self._threads.values()):
            q.put({"error": {"message": "Codex process exited"}, "method": "_dead"})
        self.state = "error"
        self.last_error = "Codex process exited; it will restart on the next request."

    def _write(self, obj):
        with self._wlock:
            try:
                self._proc.stdin.write(json.dumps(obj) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError, AttributeError) as exc:
                raise LLMError("Codex process is not running") from exc

    def _request_locked(self, method, params, timeout=30):
        """Send a request and wait; the caller already holds self._lock (used during startup)."""
        self._next_id += 1
        rid = self._next_id
        q: queue.Queue = queue.Queue()
        self._pending[rid] = q
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty as exc:
            raise LLMError(f"Codex did not answer {method} in {timeout}s") from exc
        if "error" in msg:
            raise LLMError(f"Codex {method} failed: {msg['error'].get('message', msg['error'])}")
        return msg.get("result") or {}

    def _request(self, method, params, timeout=30):
        with self._lock:
            self._ensure_started()
            self._next_id += 1
            rid = self._next_id
            q: queue.Queue = queue.Queue()
            self._pending[rid] = q
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty as exc:
            raise LLMError(f"Codex did not answer {method} in {timeout}s") from exc
        if "error" in msg:
            raise _codex_error(f"Codex {method} failed: {msg['error'].get('message', msg['error'])}")
        return msg.get("result") or {}

    # ---------- public ----------
    def generate(self, system: str, prompt: str, on_delta=None, schema: dict | None = None,
                 effort: str = "low", timeout: float = 300) -> str:
        """Run one stateless turn. Streams text through on_delta(str); returns the full final text."""
        with self._slots:
            params = {"cwd": str(WORKDIR), "personality": "none", "ephemeral": True, "sandbox": "read-only",
                      "approvalPolicy": "never", "baseInstructions": system}
            if self.chosen:
                params["model"] = self.chosen
            try:
                started = self._request("thread/start", params)
            except LLMError as exc:
                self.state, self.last_error = "error", str(exc)
                raise
            self.model = started.get("model") or self.model
            thread_id = started["thread"]["id"]
            events: queue.Queue = queue.Queue()
            self._threads[thread_id] = events
            try:
                params = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}], "effort": effort}
                if schema:
                    params["outputSchema"] = schema
                self._request("turn/start", params)
                self.state = "ready"
                return self._collect(events, on_delta, timeout)
            finally:
                self._threads.pop(thread_id, None)

    def _collect(self, events: queue.Queue, on_delta, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        streamed, final = [], None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMError("The AI took too long to answer. Try again, or use fewer sources.")
            try:
                msg = events.get(timeout=remaining)
            except queue.Empty:
                continue
            method, params = msg.get("method", ""), msg.get("params") or {}
            if method == "_dead":
                raise LLMError(msg["error"]["message"])
            if method == "item/agentMessage/delta":
                streamed.append(params.get("delta", ""))
                if on_delta:
                    on_delta(params.get("delta", ""))
            elif method == "item/completed" and (params.get("item") or {}).get("type") == "agentMessage":
                final = params["item"].get("text", "")
            elif method == "error" and not params.get("willRetry"):
                raise _codex_error((params.get("error") or {}).get("message") or "The AI returned an error.")
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("error"):
                    raise _codex_error(turn["error"].get("message") or "The AI turn failed.")
                return final if final is not None else "".join(streamed)

    def generate_json(self, system: str, prompt: str, schema: dict, effort: str = "low", timeout: float = 300) -> dict:
        return parse_json(self.generate(system, prompt, schema=schema, effort=effort, timeout=timeout))

    def list_models(self) -> list[dict]:
        rows = self._request("model/list", {}).get("data") or []
        return [{"id": m.get("model") or m["id"], "label": m.get("displayName") or m["id"],
                 "default": bool(m.get("isDefault"))} for m in rows if not m.get("hidden")]

    def warm_up(self):
        """Start the process and pay the slow first turn in the background."""
        try:
            self.generate("Reply with OK.", "OK?", effort="low", timeout=90)
        except Exception as exc:  # status endpoint reports it
            self.state, self.last_error = "error", str(exc)

    def status(self) -> dict:
        alive = bool(self._proc and self._proc.poll() is None)
        return {"state": self.state if alive or self.state == "error" else "idle",
                "model": self.model, "error": self.last_error if self.state == "error" else ""}


# ---------- provider switch ----------
PROVIDERS = {
    "codex": {"label": "ChatGPT", "kind": "connector", "note": "Your ChatGPT plan through the Codex CLI login (run: codex login)"},
    "anthropic": {"label": "Claude", "kind": "key", "default": "claude-opus-5-5", "keys_url": "https://console.anthropic.com/settings/keys"},
    "openai": {"label": "OpenAI", "kind": "key", "keys_url": "https://platform.openai.com/api-keys"},
    "gemini": {"label": "Gemini", "kind": "key", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "keys_url": "https://aistudio.google.com/apikey", "note": "Google AI Studio has a free tier"},
    "openrouter": {"label": "OpenRouter", "kind": "key", "base_url": "https://openrouter.ai/api/v1",
                   "keys_url": "https://openrouter.ai/keys", "note": "One key, many models; some are free"},
}


def _keys_path() -> Path:
    from . import db
    return db.DATA_DIR / "keys.json"


def _stored_keys() -> dict:
    try:
        return json.loads(_keys_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_key(provider: str) -> str:
    return os.environ.get(f"CUELINE_{provider.upper()}_API_KEY") or _stored_keys().get(provider, "")


def set_key(provider: str, key: str | None) -> None:
    keys = _stored_keys()
    if key:
        keys[provider] = key
    else:
        keys.pop(provider, None)
    path = _keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(keys, f)
    os.replace(tmp, path)


def build_engine(provider: str, key: str, model: str):
    from . import llm_api
    if provider == "anthropic":
        return llm_api.AnthropicEngine(key, model)
    p = PROVIDERS[provider]
    return llm_api.OpenAICompatEngine(key, model, provider, p["label"], p.get("base_url"))


class Engine:
    """Routes calls to the chosen provider. Choice lives in settings: ai_provider, ai_model_<provider>."""

    PAUSE_STEPS = (30 * 60, 60 * 60, 2 * 3600, 6 * 3600)  # unknown wait: 30 min, then longer on repeated hits

    def __init__(self):
        self.codex = CodexEngine()
        self._api: dict[str, tuple] = {}  # provider -> ((key, model), engine)
        self._pause: dict[str, dict] = {}  # provider -> {"until": epoch, "hits": n}

    def choice(self) -> tuple[str, str]:
        from . import db
        try:
            provider = db.setting("ai_provider", "codex")
            if provider not in PROVIDERS:
                provider = "codex"
            model = db.setting(f"ai_model_{provider}", "") or PROVIDERS[provider].get("default", "")
        except Exception:  # no database yet (e.g. a tool run before init): fall back to Codex's default
            return "codex", ""
        return provider, model

    def current(self):
        provider, model = self.choice()
        if provider == "codex":
            self.codex.chosen = model or None
            return self.codex
        key = get_key(provider)
        if not key:
            raise LLMError(f"Add your {PROVIDERS[provider]['label']} API key first (top bar → AI model).")
        cached = self._api.get(provider)
        if not cached or cached[0] != (key, model):
            cached = ((key, model), build_engine(provider, key, model))
            self._api[provider] = cached
        return cached[1]

    @property
    def model(self):
        provider, model = self.choice()
        return (self.codex.model if provider == "codex" and not model else model) or None

    def generate(self, *args, **kwargs) -> str:
        return self._call("generate", *args, **kwargs)

    def generate_json(self, *args, **kwargs) -> dict:
        return self._call("generate_json", *args, **kwargs)

    def _call(self, name, *args, **kwargs):
        """User actions always try (the limit may have reset); background work checks paused() first.
        A quota error pauses background AI work for this provider; any success clears the pause."""
        provider = self.choice()[0]
        try:
            out = getattr(self.current(), name)(*args, **kwargs)
        except QuotaError as exc:
            hits = self._pause.get(provider, {}).get("hits", 0) + 1
            wait = exc.retry_after or self.PAUSE_STEPS[min(hits, len(self.PAUSE_STEPS)) - 1]
            until = time.time() + max(60.0, min(wait, 6 * 3600.0))
            self._pause[provider] = {"until": until, "hits": hits}
            at = time.strftime("%H:%M", time.localtime(until))
            raise QuotaError(f"{PROVIDERS[provider]['label']} usage limit reached. Background reading resumes around {at}; "
                             "to keep working now, switch model (top bar → AI model).", exc.retry_after) from exc
        self._pause.pop(provider, None)
        return out

    def paused(self) -> float | None:
        """Epoch until which background AI work should wait for the current provider, or None."""
        p = self._pause.get(self.choice()[0])
        return p["until"] if p and p["until"] > time.time() else None

    def list_models(self, provider: str, key: str | None = None) -> list[dict]:
        if provider == "codex":
            return self.codex.list_models()
        key = key or get_key(provider)
        if not key:
            raise LLMError(f"Add your {PROVIDERS[provider]['label']} API key first.")
        eng = build_engine(provider, key, "")
        try:
            return eng.list_models()
        except Exception as exc:
            raise LLMError(eng._friendly(exc)) from exc

    def warm_up(self):
        if self.choice()[0] == "codex":
            self.codex.warm_up()

    def status(self) -> dict:
        provider, _ = self.choice()
        base = {"provider": provider, "label": PROVIDERS[provider]["label"]}
        try:
            eng = self.current()
        except LLMError as exc:
            return {**base, "state": "error", "model": self.model, "error": str(exc)}
        out = {**base, **eng.status(), "model": self.model}
        until = self.paused()
        if until:
            out.update(state="paused", paused_until=datetime.fromtimestamp(until, timezone.utc).isoformat(timespec="seconds"))
        return out


engine = Engine()
