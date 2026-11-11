"""Language-model clients that return JSON matching a schema.

Two implementations:
- OllamaLLM: a local model served by Ollama (free, transcripts stay on this machine).
- ClaudeLLM: Anthropic's API (opt-in via TRANSCRIBLY_AI_PROVIDER=claude; transcripts are sent to Anthropic).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Protocol

import httpx

from ..config import get_settings
from ..errors import Cancelled

log = logging.getLogger(__name__)

Progress = Callable[[int], None]  # called with the number of characters generated so far


class LLMError(Exception):
    """The model could not produce usable output. The message is safe to show."""


class LLM(Protocol):
    label: str  # human-readable, e.g. "Qwen 2.5 7B (Ollama, on this server)"
    method: str  # stored with the notes, e.g. "ai:ollama:qwen2.5:7b"
    context_tokens: int  # how much transcript + output fits in one request

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any],
                      on_progress: Progress | None = None,
                      should_cancel: Callable[[], bool] | None = None) -> dict[str, Any]: ...


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def _parse(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError("The AI model returned malformed output.") from e
    if not isinstance(data, dict):
        raise LLMError("The AI model returned malformed output.")
    return data


def _pretty_model(name: str) -> str:
    base, _, size = name.partition(":")
    pretty = {"qwen2.5": "Qwen 2.5", "qwen3": "Qwen 3", "llama3.1": "Llama 3.1", "llama3.2": "Llama 3.2",
              "gemma3": "Gemma 3", "mistral": "Mistral", "phi4": "Phi-4"}.get(base, base)
    return f"{pretty} {size.upper()}".strip() if size else pretty


class OllamaLLM:
    def __init__(self, url: str, model: str, num_ctx: int, timeout: int,
                 transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self.model = model
        self.context_tokens = num_ctx
        self.label = f"{_pretty_model(model)}, an open-source model running on this server"
        self.method = f"ai:ollama:{model}"
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), transport=transport)

    def available(self) -> bool:
        try:
            r = self.client.get(f"{self.url}/api/tags", timeout=3.0)
            r.raise_for_status()
            names = {m.get("name") for m in r.json().get("models", [])}
            return self.model in names or f"{self.model}:latest" in names
        except Exception:
            return False

    def generate_json(self, system, prompt, schema, on_progress=None, should_cancel=None):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "format": schema,  # Ollama constrains decoding to this JSON schema
            "stream": True,
            "keep_alive": "2m",  # stays loaded between the parts of one job; released afterwards
            "options": {"num_ctx": self.context_tokens, "temperature": 0.2},
        }
        out: list[str] = []
        chars = 0
        done_reason = None
        t0 = time.perf_counter()
        try:
            with self.client.stream("POST", f"{self.url}/api/chat", json=body) as r:
                if r.status_code == 404:
                    raise LLMError(f"The AI model “{self.model}” is not installed on this server.")
                if r.status_code >= 400:
                    detail = r.read().decode(errors="replace").lower()
                    if "memory" in detail:
                        raise LLMError("This server doesn't have enough free memory to run the AI model.")
                    raise LLMError("The local AI model reported an error.")
                for line in r.iter_lines():
                    if should_cancel and should_cancel():
                        raise Cancelled()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise LLMError("The local AI model reported an error.")
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        out.append(piece)
                        chars += len(piece)
                        if on_progress:
                            on_progress(chars)
                    if chunk.get("done"):
                        done_reason = chunk.get("done_reason")
                        log.info("ollama %s: prompt=%s tok, output=%s tok in %.1fs", self.model,
                                 chunk.get("prompt_eval_count"), chunk.get("eval_count"), time.perf_counter() - t0)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise LLMError("The local AI model (Ollama) isn't running.") from e
        except httpx.HTTPError as e:
            raise LLMError("The local AI model stopped responding.") from e
        if done_reason == "length":
            raise LLMError("The AI model ran out of room before finishing its notes.")
        return _parse("".join(out))

    def release(self) -> None:
        """Unload the model so its memory is free for speech recognition."""
        try:
            self.client.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=5.0)
        except Exception:
            pass


class ClaudeLLM:
    """Claude via the official Anthropic SDK. Credentials come from the environment
    (ANTHROPIC_API_KEY, or an `ant auth login` profile) - never from this app's settings."""

    def __init__(self, model: str, effort: str, client: Any | None = None):
        import anthropic

        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.context_tokens = 600_000  # well inside the 1M window, leaving room for thinking + output
        self.label = f"Claude {model} via the Anthropic API"
        self.method = f"ai:claude:{model}"

    def generate_json(self, system, prompt, schema, on_progress=None, should_cancel=None):
        a = self.anthropic
        chars = 0
        try:
            # Streaming avoids HTTP timeouts on long inputs/outputs. "default" fallbacks let the API
            # retry on another model if a safety classifier declines the request.
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=64000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for text in stream.text_stream:
                    if should_cancel and should_cancel():
                        raise Cancelled()
                    chars += len(text)
                    if on_progress:
                        on_progress(chars)
                message = stream.get_final_message()
        except a.AuthenticationError as e:
            raise LLMError("The Anthropic API key is missing or invalid.") from e
        except a.RateLimitError as e:
            raise LLMError("The Anthropic API is rate limiting requests. Try again shortly.") from e
        except a.APIStatusError as e:
            log.warning("claude request failed: status=%s request_id=%s", e.status_code,
                        getattr(e, "request_id", None))
            raise LLMError("The Anthropic API returned an error.") from e
        except a.APIConnectionError as e:
            raise LLMError("Couldn't reach the Anthropic API.") from e
        if message.stop_reason == "refusal":
            raise LLMError("The AI model declined to summarize this content.")
        if message.stop_reason == "max_tokens":
            raise LLMError("The AI model ran out of room before finishing its notes.")
        text = next((b.text for b in message.content if getattr(b, "type", None) == "text"), "")
        return _parse(text)

    def release(self) -> None:
        pass


_ollama_cache: dict[str, tuple[float, bool]] = {}


def get_llm() -> LLM | None:
    """The configured AI writer, or None when AI notes are disabled or unavailable."""
    s = get_settings()
    provider = s.ai_provider.lower()
    if provider == "none" or s.transcription_engine == "mock" and provider == "auto":
        return None
    if provider == "claude":
        try:
            return ClaudeLLM(s.claude_model, s.claude_effort)
        except Exception as e:
            log.warning("Claude unavailable: %s", type(e).__name__)
            return None
    if provider in ("auto", "ollama"):
        llm = OllamaLLM(s.ollama_url, s.ollama_model, s.ollama_num_ctx, s.ollama_timeout_seconds)
        key = f"{s.ollama_url}|{s.ollama_model}"
        cached = _ollama_cache.get(key)
        if cached and time.time() - cached[0] < 30:
            ok = cached[1]
        else:
            ok = llm.available()
            _ollama_cache[key] = (time.time(), ok)
        return llm if ok else None
    return None


def free_ai_memory() -> None:
    """Before speech recognition: make sure a local AI model isn't holding the RAM Whisper needs."""
    s = get_settings()
    if s.ai_provider.lower() in ("auto", "ollama") and s.transcription_engine != "mock":
        OllamaLLM(s.ollama_url, s.ollama_model, s.ollama_num_ctx, 5).release()


def ai_status() -> dict[str, Any]:
    """Public, non-sensitive description for the UI."""
    llm = get_llm()
    return {"available": llm is not None, "label": llm.label if llm else None,
            "provider": llm.method.split(":")[1] if llm else None}
