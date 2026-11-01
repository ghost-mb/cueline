"""Image generation for Studio (AI-image infographics). Provider is a setting (AI model dialog → Images):

- "codex" (default): a SECOND `codex app-server` process on the user's ChatGPT login, with only image generation on.
  Codex's image tool needs its code-mode host, so that one is on too; everything else stays off, the working dir is
  an empty folder, the sandbox is read-only, and the image thread only ever sees a short brief written by our own
  text step (never raw source text). The model's text reply is discarded; we keep only the PNG.
- "openai" / "gemini": the user's API key (same keys as the text providers), official OpenAI SDK, billed per image.

    png_bytes = images.generate(prompt, shape="portrait")
"""
import base64
import queue
import threading
import time

from . import llm
from .llm import CodexEngine, LLMError, QuotaError, _codex_error

PROVIDERS = {
    "codex": {"label": "ChatGPT", "note": "Uses your ChatGPT plan quota through the Codex CLI login"},
    "openai": {"label": "OpenAI", "default": "gpt-image-1", "note": "Billed to your OpenAI key per image"},
    "gemini": {"label": "Gemini", "default": "imagen-4.0-generate-001", "note": "Billed to your Gemini key per image"},
}
SIZES = {"portrait": ("1024x1536", "3:4 portrait"), "square": ("1024x1024", "1:1 square"),
         "landscape": ("1536x1024", "16:9 landscape")}
IMAGE_TIMEOUT = 240

class NoImage(LLMError):
    """The turn ended without an image (the model skipped the tool)."""


CODEX_INSTRUCTIONS = ("You are an image generator. Call your image generation tool exactly once with the user's "
                      "brief, unchanged in meaning, then reply with the single word DONE. Never do anything else.")


class CodexImageEngine(CodexEngine):
    ENABLED = ("image_generation", "code_mode_host")

    def __init__(self):
        super().__init__()
        self._slots = threading.Semaphore(1)  # one image at a time: they are slow and eat the shared quota

    def _command(self) -> list[str]:
        cmd = [llm.CODEX_BIN, "app-server"]
        for feature in llm.DISABLED_FEATURES:
            if feature not in self.ENABLED:
                cmd += ["-c", f"features.{feature}=false"]
        for feature in self.ENABLED:
            cmd += ["-c", f"features.{feature}=true"]
        cmd += ["-c", 'web_search="disabled"']
        for name in llm._mcp_server_names():
            cmd += ["-c", f"mcp_servers.{name}.enabled=false"]
        return cmd

    def image(self, prompt: str, timeout: float = IMAGE_TIMEOUT) -> bytes:
        """The model sometimes answers without calling the tool: one retry with a firmer nudge."""
        try:
            return self._image_once(prompt, timeout)
        except NoImage:
            return self._image_once("Generate this image now with your image generation tool.\n\n" + prompt, timeout)

    def _image_once(self, prompt: str, timeout: float) -> bytes:
        with self._slots:
            started = self._request("thread/start", {
                "cwd": str(llm.WORKDIR), "personality": "none", "ephemeral": True, "sandbox": "read-only",
                "approvalPolicy": "never", "baseInstructions": CODEX_INSTRUCTIONS})
            thread_id = started["thread"]["id"]
            events: queue.Queue = queue.Queue()
            self._threads[thread_id] = events
            try:
                self._request("turn/start", {"threadId": thread_id, "effort": "low",
                                             "input": [{"type": "text", "text": prompt}]})
                return self._collect_image(events, timeout)
            finally:
                self._threads.pop(thread_id, None)

    def _collect_image(self, events: queue.Queue, timeout: float) -> bytes:
        deadline, png, failure, said = time.monotonic() + timeout, None, "", ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMError("The image took too long. Try again.")
            try:
                msg = events.get(timeout=remaining)
            except queue.Empty:
                continue
            method, params = msg.get("method", ""), msg.get("params") or {}
            item = params.get("item") or {}
            if method == "_dead":
                raise LLMError(msg["error"]["message"])
            if method == "item/completed" and item.get("type") == "imageGeneration":
                if item.get("result"):
                    png = base64.b64decode(item["result"])
                else:
                    failure = str(item.get("failure") or "")
            elif method == "item/completed" and item.get("type") == "agentMessage":
                said = (item.get("text") or "").strip()
            elif method == "error" and not params.get("willRetry"):
                raise _codex_error((params.get("error") or {}).get("message") or "Image generation failed.")
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("error"):
                    raise _codex_error(turn["error"].get("message") or "Image generation failed.")
                if png:
                    return png
                if failure:
                    raise _codex_error(f"ChatGPT couldn't make the image: {failure}")
                raise NoImage("ChatGPT didn't make an image" + (f" (it said: {said[:200]})" if said and said != "DONE" else "")
                              + ". Try again, or pick another image engine in the AI model dialog.")


codex = CodexImageEngine()


def choice() -> tuple[str, str]:
    from . import db
    provider = db.setting("image_provider", "codex")
    if provider not in PROVIDERS:
        provider = "codex"
    return provider, db.setting(f"image_model_{provider}", "") or PROVIDERS[provider].get("default", "")


def _api_image(provider: str, model: str, prompt: str, shape: str) -> bytes:
    from openai import OpenAI
    import openai
    key = llm.get_key(provider)
    if not key:
        raise LLMError(f"Add your {PROVIDERS[provider]['label']} API key first (top bar → AI model).")
    client = OpenAI(api_key=key, base_url=llm.PROVIDERS[provider].get("base_url"), timeout=IMAGE_TIMEOUT, max_retries=1)
    kwargs = {"model": model, "prompt": prompt, "n": 1}
    if provider == "openai":
        kwargs["size"] = SIZES[shape][0]  # gpt-image models always return base64
    else:
        kwargs["response_format"] = "b64_json"
    try:
        out = client.images.generate(**kwargs)
    except openai.RateLimitError as exc:
        raise QuotaError(f"{PROVIDERS[provider]['label']} image limit reached: {exc}") from exc
    except openai.APIError as exc:
        raise LLMError(f"{PROVIDERS[provider]['label']} image failed: {getattr(exc, 'message', exc)}") from exc
    data = out.data[0] if out.data else None
    if not data or not data.b64_json:
        raise LLMError("The image service returned no image. Try again.")
    return base64.b64decode(data.b64_json)


def generate(prompt: str, shape: str = "portrait") -> bytes:
    """One image as PNG bytes. shape: portrait | square | landscape."""
    shape = shape if shape in SIZES else "portrait"
    prompt = f"{prompt.strip()}\n\nAspect ratio: {SIZES[shape][1]}."
    provider, model = choice()
    if provider == "codex":
        return codex.image(prompt)
    return _api_image(provider, model, prompt, shape)


def status() -> dict:
    provider, model = choice()
    return {"provider": provider, "label": PROVIDERS[provider]["label"], "model": model if provider != "codex" else ""}
