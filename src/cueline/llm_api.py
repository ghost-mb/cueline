"""API-key engines: Claude (official Anthropic SDK) and OpenAI-compatible APIs (official OpenAI SDK) for OpenAI,
Gemini and OpenRouter. Same interface as llm.CodexEngine: generate() / generate_json() / list_models() / status().

Plain text in, text out: no tools are ever sent, so the model can only write (same guarantee as the Codex engine).
Keys never leave this process except to their own provider; errors are mapped to short user-facing messages.
"""
import json
import re
import threading

import anthropic
import openai

from .llm import LLMError, QuotaError, parse_json

MAX_PARALLEL = 3
MAX_TOKENS = 32000  # long HTML pages fit; streamed, so no HTTP timeout issue

# Claude models that take server-side refusal fallbacks (beta). The rest of the family just stops on a refusal.
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_EFFORT = ("claude-3", "haiku-4-5", "sonnet-4-5")  # older Claude models reject output_config.effort


class ApiEngine:
    label = "API"

    def __init__(self, key: str, model: str):
        self.key, self.model = key, model
        self._slots = threading.Semaphore(MAX_PARALLEL)
        self.state, self.last_error = "ready", ""

    def generate(self, system: str, prompt: str, on_delta=None, schema: dict | None = None,
                 effort: str = "low", timeout: float = 300) -> str:
        if not self.model:
            raise LLMError(f"Pick a {self.label} model first (top bar → AI model).")
        with self._slots:
            try:
                text = self._generate(system, prompt, on_delta, schema, effort, timeout)
            except LLMError as exc:
                self.state, self.last_error = "error", str(exc)
                raise
            except Exception as exc:
                self.state, self.last_error = "error", self._friendly(exc)
                if isinstance(exc, (anthropic.RateLimitError, openai.RateLimitError)):
                    raise QuotaError(self.last_error, _retry_after(exc)) from exc
                raise LLMError(self.last_error) from exc
            self.state, self.last_error = "ready", ""
            return text

    def generate_json(self, system: str, prompt: str, schema: dict, effort: str = "low", timeout: float = 300) -> dict:
        return parse_json(self.generate(system, prompt, schema=schema, effort=effort, timeout=timeout))

    def status(self) -> dict:
        return {"state": self.state, "model": self.model, "error": self.last_error if self.state == "error" else ""}

    def _friendly(self, exc: Exception) -> str:
        for mod in (anthropic, openai):
            if isinstance(exc, mod.AuthenticationError) or (
                    isinstance(exc, mod.BadRequestError) and "api key" in _message(exc).lower()):  # Gemini: 400
                return f"Your {self.label} API key was rejected. Check it under AI model."
            if isinstance(exc, mod.PermissionDeniedError):
                return f"Your {self.label} key can't use {self.model}."
            if isinstance(exc, mod.NotFoundError):
                return f"{self.label} doesn't know the model {self.model}. Pick another under AI model."
            if isinstance(exc, mod.RateLimitError):
                return f"{self.label} rate limit or quota reached. Try again later, or switch model under AI model."
            if isinstance(exc, mod.APITimeoutError):
                return "The AI took too long to answer. Try again, or use fewer sources."
            if isinstance(exc, mod.APIConnectionError):
                if getattr(self, "provider", "") == "local":
                    return (f"No local model server answers at {self.client.base_url}. Start Ollama or LM Studio, "
                            "or set CUELINE_LOCAL_URL.")
                return f"Can't reach {self.label}. Check the internet connection."
            if isinstance(exc, mod.APIStatusError):
                return f"{self.label} error ({exc.status_code}): {_message(exc)}"
        return str(exc) or "The AI returned an error."


def _message(exc) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    return str(getattr(exc, "message", exc))[:300]


def _retry_after(exc) -> float | None:
    try:
        return float(exc.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None


def _json_instruction(schema: dict) -> str:
    return ("\n\nReply with only a JSON object (no prose, no code fences) that matches this JSON schema:\n"
            + json.dumps(schema))


# ---------- Claude ----------
class AnthropicEngine(ApiEngine):
    label = "Claude"

    def __init__(self, key: str, model: str):
        super().__init__(key, model)
        self.client = anthropic.Anthropic(api_key=key, max_retries=2)

    def _generate(self, system, prompt, on_delta, schema, effort, timeout):
        use_effort = not any(tag in self.model for tag in NO_EFFORT)
        use_format = bool(schema)
        use_fallbacks = self.model in FALLBACK_MODELS
        for _ in range(4):  # drop an optional feature the model rejects, then retry
            output_config = {}
            if use_effort:
                output_config["effort"] = effort
            if use_format:
                output_config["format"] = {"type": "json_schema", "schema": schema}
            user = prompt + (_json_instruction(schema) if schema and not use_format else "")
            params = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system,
                      "messages": [{"role": "user", "content": user}]}
            if output_config:
                params["output_config"] = output_config
            client = self.client.with_options(timeout=timeout)
            try:
                if use_fallbacks:
                    stream_cm = client.beta.messages.stream(**params, betas=[FALLBACK_BETA], fallbacks="default")
                else:
                    stream_cm = client.messages.stream(**params)
                with stream_cm as stream:
                    for text in stream.text_stream:
                        if on_delta:
                            on_delta(text)
                    msg = stream.get_final_message()
            except anthropic.BadRequestError as exc:
                text = _message(exc).lower()
                if use_fallbacks and "fallback" in text:
                    use_fallbacks = False
                elif use_effort and "effort" in text:
                    use_effort = False
                elif use_format and ("format" in text or "schema" in text or "output_config" in text):
                    use_format = False
                else:
                    raise
                continue
            if msg.stop_reason == "refusal":
                raise LLMError("The model declined this request. Try rewording it or another model.")
            # After a server-side fallback the answer is the text after the last fallback block.
            blocks = list(msg.content)
            last_fb = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
            out = "".join(b.text for b in blocks[last_fb + 1:] if b.type == "text")
            if msg.stop_reason == "max_tokens":
                raise LLMError("The answer was too long and got cut off. Try a shorter output or fewer sources.")
            return out
        raise LLMError("Claude rejected the request.")

    def list_models(self) -> list[dict]:
        return [{"id": m.id, "label": m.display_name or m.id} for m in self.client.models.list(limit=100)]


# ---------- OpenAI-compatible (OpenAI, Gemini, OpenRouter) ----------
SKIP_OPENAI = re.compile(r"audio|realtime|tts|transcribe|whisper|dall-e|image|embedding|moderation|search|codex|instruct|babbage|davinci")
SKIP_GEMINI = re.compile(r"embedding|imagen|veo|tts|live|image|aqa|learnlm|gemma")


class OpenAICompatEngine(ApiEngine):
    def __init__(self, key: str, model: str, provider: str, label: str, base_url: str | None = None):
        super().__init__(key, model)
        self.provider, self.label = provider, label
        headers = {"X-Title": "Cueline"} if provider == "openrouter" else None
        retries = 0 if provider == "local" else 2  # a refused local connection won't fix itself in a second
        self.client = openai.OpenAI(api_key=key, base_url=base_url, max_retries=retries, default_headers=headers)

    def _generate(self, system, prompt, on_delta, schema, effort, timeout):
        use_effort, use_format = True, bool(schema)
        for _ in range(4):
            user = prompt + (_json_instruction(schema) if schema and not use_format else "")
            kwargs = {"model": self.model, "stream": True,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if use_format:
                kwargs["response_format"] = {"type": "json_schema",
                                             "json_schema": {"name": "cueline_output", "schema": schema, "strict": True}}
            if use_effort:
                if self.provider == "openrouter":
                    kwargs["extra_body"] = {"reasoning": {"effort": effort}}
                else:
                    kwargs["reasoning_effort"] = effort
            try:
                parts, finish = [], None
                for chunk in self.client.with_options(timeout=timeout).chat.completions.create(**kwargs):
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta.content if choice.delta else None
                    if delta:
                        parts.append(delta)
                        if on_delta:
                            on_delta(delta)
                    finish = choice.finish_reason or finish
            except openai.BadRequestError as exc:
                text = _message(exc).lower()
                if use_effort and ("reasoning" in text or "effort" in text):
                    use_effort = False
                elif use_format and ("response_format" in text or "json_schema" in text or "schema" in text):
                    use_format = False
                else:
                    raise
                continue
            if finish == "length":
                raise LLMError("The answer was too long and got cut off. Try a shorter output or fewer sources.")
            if finish == "content_filter":
                raise LLMError("The model declined this request. Try rewording it or another model.")
            return "".join(parts)
        raise LLMError(f"{self.label} rejected the request.")

    def list_models(self) -> list[dict]:
        if self.provider == "openrouter":  # its model list is public, so check the key itself first
            self.client.get("/key", cast_to=object)
        rows = []
        for m in self.client.models.list():
            mid = m.id.removeprefix("models/")
            extra = getattr(m, "model_extra", None) or {}
            if self.provider == "openai" and (not re.match(r"(gpt-|o\d|chatgpt)", mid) or SKIP_OPENAI.search(mid)):
                continue
            if self.provider == "gemini" and (not mid.startswith("gemini") or SKIP_GEMINI.search(mid)):
                continue
            rows.append({"id": mid, "label": extra.get("name") or extra.get("display_name") or mid})
        if self.provider == "openrouter":
            rows.sort(key=lambda r: (not r["id"].endswith(":free"), r["id"]))
        else:
            rows.sort(key=lambda r: r["id"], reverse=True)  # newest versions first
        return rows
