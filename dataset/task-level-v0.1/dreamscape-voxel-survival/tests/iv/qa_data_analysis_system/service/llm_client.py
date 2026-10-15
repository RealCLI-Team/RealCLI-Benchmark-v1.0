#!/usr/bin/env python3
"""Thin OpenAI-compatible chat client for LLM annotation.

This module owns *only* the HTTP contract with the LLM endpoint: base URL,
bearer auth, per-call timeout, bounded retry/backoff, and tolerant extraction of
a JSON object from the model's reply. It knows nothing about taxonomy or jobs so
it can be unit-tested against a stub URL.

The endpoint is OpenAI-compatible (`/v1/chat/completions`). Note the provided
deployment uses a doubled `/v1/` path, e.g. base URL ending in `.../v1/4000`
followed by `/v1/chat/completions`. Configure via env or constructor.

Models on this endpoint do not strictly honour a requested JSON schema (e.g.
returning `confidence: 0.9` when a string was asked for), so the parser here is
deliberately tolerant and callers must validate/repair semantics themselves.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


LOG = logging.getLogger("llm_client")


# First balanced-looking JSON object in a blob of text. Non-greedy on the outer
# braces is wrong for nested objects, so we scan for balanced braces instead.
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


class LLMError(RuntimeError):
    """Raised when a call ultimately fails after retries, or returns no usable JSON."""


# ---------------------------------------------------------------------------
# Shared model pricing — single source of truth for cost across the Web UI.
# ---------------------------------------------------------------------------
# Price per 1M tokens in USD as (base_input, output). We meter only prompt +
# completion tokens (no cache-read/write counters), so cost intentionally uses
# base-input + output rates; cache tiers are not modelled. Cost is an ESTIMATE
# surfaced for budgeting, not a billed figure, and can be overridden per-call
# via QA_LLM_PRICE_PROMPT / QA_LLM_PRICE_COMPLETION.
#
# Keys are the NORMALIZED model name (lowercase, alphanumerics only) so friendly
# ids ("Claude-Sonnet-4.6"), dated ids ("claude-opus-4-5-20251101") and spaced
# labels ("Claude Opus 4.8") all resolve via longest-prefix match in
# ``price_per_1m`` — see ``_norm_model``.
MODEL_PRICING = {
    # Claude — from the published rate card (base input, output).
    "claudefable5":   (10.0, 50.0),
    "claudemythos5":  (10.0, 50.0),
    "claudeopus5":    (5.0, 25.0),
    "claudeopus48":   (5.0, 25.0),
    "claudeopus47":   (5.0, 25.0),
    "claudeopus46":   (5.0, 25.0),
    "claudeopus45":   (5.0, 25.0),
    "claudeopus41":   (15.0, 75.0),
    "claudeopus4":    (15.0, 75.0),
    "claudesonnet46": (3.0, 15.0),
    "claudehaiku45":  (1.0, 5.0),
    # Non-Claude placeholders (endpoint publishes no pricing).
    "deepseekv4flash": (0.10, 0.30),
    "deepseekv4pro":   (0.50, 1.50),
    "glm52":           (0.30, 0.90),
    "gemini3pro":      (1.25, 5.00),
    "gemini35flash":   (0.075, 0.30),
}

# Claude Sonnet 5 is date-tiered: $2/$10 through 2026-08-31, then $3/$15.
_SONNET5_KEY = "claudesonnet5"
_SONNET5_SWITCH = (2026, 9, 1)  # first day of the higher tier


def _norm_model(model: str) -> str:
    """Lowercase, keep alphanumerics only: 'Claude-Opus-4.8' -> 'claudeopus48'."""
    return "".join(ch for ch in (model or "").lower() if ch.isalnum())


def _sonnet5_price() -> tuple[float, float]:
    import datetime
    y, m, d = _SONNET5_SWITCH
    return (3.0, 15.0) if datetime.date.today() >= datetime.date(y, m, d) else (2.0, 10.0)


def price_per_1m(model: str) -> tuple[float, float]:
    """(base_input, output) USD per 1M tokens for ``model``.

    Resolution order: explicit env override → date-tiered Sonnet 5 → exact
    normalized key → longest normalized key that is a prefix of the model
    (so dated ids like 'claude-opus-4-5-20251101' resolve to 'claudeopus45')
    → generic placeholder.
    """
    env_p = os.environ.get("QA_LLM_PRICE_PROMPT")
    env_c = os.environ.get("QA_LLM_PRICE_COMPLETION")
    if env_p is not None and env_c is not None:
        return float(env_p), float(env_c)
    n = _norm_model(model)
    # 'claudesonnet5' exact or a dated variant ('claudesonnet5YYYYMMDD'); the
    # trailing chars after the key are a date, never a minor version in this
    # table (Sonnet 4.6 normalizes to 'claudesonnet46', not '...5').
    if n == _SONNET5_KEY or n.startswith(_SONNET5_KEY):
        return _sonnet5_price()
    if n in MODEL_PRICING:
        return MODEL_PRICING[n]
    best = ""
    for key in MODEL_PRICING:
        if n.startswith(key) and len(key) > len(best):
            best = key
    if best:
        return MODEL_PRICING[best]
    return (0.20, 0.60)


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p, c = price_per_1m(model)
    return round(prompt_tokens / 1_000_000 * p + completion_tokens / 1_000_000 * c, 6)


@dataclass
class LLMResponse:
    content: str
    raw: dict[str, Any]
    latency_s: float
    model: str
    attempts: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _first_json_object(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` substring, or None.

    Handles nested braces and braces inside double-quoted strings. This is
    forgiving of leading prose ("Here is the JSON:") and trailing commentary.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(content: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object from a model reply.

    Strategy: try the whole string, then any fenced block, then the first
    balanced brace span. Raises LLMError if nothing parses.
    """
    candidates: list[str] = []
    stripped = content.strip()
    if stripped:
        candidates.append(stripped)
    for m in _FENCE_RE.finditer(content):
        body = m.group(1).strip()
        if body:
            candidates.append(body)
    span = _first_json_object(content)
    if span:
        candidates.append(span)

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            # Try the balanced-brace slice of this candidate too.
            span = _first_json_object(cand)
            if not span or span == cand:
                continue
            try:
                obj = json.loads(span)
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(obj, dict):
            return obj
    raise LLMError("no JSON object found in model reply")


class LLMClient:
    """Blocking, thread-safe (stateless) OpenAI-compatible chat client."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 300.0,  # endpoint is healthy but slow (reasoning model); be patient
        max_retries: int = 6,
        backoff_base: float = 2.0,
        backoff_cap: float = 20.0,
        max_tokens: int = 32768,  # reasoning model burns many tokens before the JSON; give headroom
        temperature: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.max_tokens = max_tokens
        self.temperature = temperature

    @property
    def chat_url(self) -> str:
        # Accept base URLs with or without a trailing /v1 (JUDGE_API_BASE carries
        # /v1 by convention); the endpoint is always <root>/v1/chat/completions.
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return f"{base}/v1/chat/completions"

    def _payload(self, messages: list[dict[str, str]], model: Optional[str] = None) -> dict[str, Any]:
        model = model or self.model
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        # Reasoning models (deepseek-v4-*) burn the whole max_tokens budget on
        # chain-of-thought and return empty content. Send every knob the local
        # OpenAI-compatible gateways accept: Qwen-style enable_thinking (both
        # top-level and chat_template_kwargs) and OpenAI-style thinking.type.
        # Verified against llm-sidecar on 2026-09-15: all three disable
        # reasoning on deepseek-v4-flash-0731-cubi; the *-siflow routes ignore
        # every knob and always think — do not route deepseek gen to -siflow.
        if "deepseek" in model.lower():
            payload["enable_thinking"] = False
            payload["thinking"] = {"type": "disabled"}
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def request_body(self, messages: list[dict[str, str]], model: Optional[str] = None) -> str:
        """The exact JSON request-body string POSTed to the endpoint (for logging)."""
        return json.dumps(self._payload(messages, model), ensure_ascii=False)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.chat_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Surface the endpoint's error body (e.g. transient "No connected db")
            # so retry logging is meaningful instead of a bare "400".
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # pragma: no cover - defensive
                detail = ""
            raise urllib.error.HTTPError(
                exc.url, exc.code, f"{exc.reason} :: {detail}", exc.headers, None
            ) from None

    def _backoff(self, attempt: int, rand=None) -> float:
        import random

        rand = rand or random.random
        delay = min(self.backoff_base ** attempt, self.backoff_cap)
        return delay + rand() * delay * 0.25  # up to 25% jitter

    def complete(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        sleep=time.sleep,
    ) -> LLMResponse:
        """Call chat completions with retry/backoff. Returns the assistant text.

        ``sleep`` is injectable so tests need not wait on backoff.
        """
        model = model or self.model
        payload = self._payload(messages, model)
        last_err: Optional[Exception] = None
        started = time.time()
        for attempt in range(1, self.max_retries + 1):
            call_start = time.time()
            try:
                raw = self._post(payload)
                content = raw["choices"][0]["message"]["content"] or ""
                latency = time.time() - call_start
                usage = raw.get("usage") or {}
                LOG.debug("model=%s attempt=%d latency=%.2fs tokens=%s ok",
                          model, attempt, latency, usage.get("total_tokens"))
                return LLMResponse(
                    content=content, raw=raw, latency_s=latency, model=model, attempts=attempt,
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(usage.get("total_tokens", 0) or 0),
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, ValueError, OSError) as exc:
                last_err = exc
                LOG.warning("model=%s attempt=%d/%d failed: %s (url=%s)", model, attempt, self.max_retries, exc, self.chat_url)
                if attempt < self.max_retries:
                    sleep(self._backoff(attempt))
        elapsed = time.time() - started
        raise LLMError(f"call failed after {self.max_retries} attempts in {elapsed:.1f}s: {last_err} (url={self.chat_url})")

    def healthy(self, model: Optional[str] = None, timeout: float = 20.0,
                probe_chars: int = 6000) -> bool:
        """Single-shot probe: is the endpoint serving *real-size* requests now?

        The flaky endpoint sometimes serves tiny prompts while failing real ~8KB
        classification calls, so the probe is padded to ``probe_chars`` to better
        match production load. One attempt, no retries.
        """
        padding = ("context " * (probe_chars // 8))[:probe_chars]
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": "You reply with a single JSON object."},
                {"role": "user", "content": f'{padding}\nReply exactly {{"ok": 1}} and nothing else.'},
            ],
            "temperature": 0.0,
            "max_tokens": 20,
        }
        old_timeout = self.timeout
        self.timeout = timeout
        try:
            raw = self._post(payload)
            return bool(raw.get("choices"))
        except Exception:
            return False
        finally:
            self.timeout = old_timeout

    def classify(self, messages: list[dict[str, str]], model: Optional[str] = None, sleep=time.sleep,
                 parse_attempts: int = 3, validate=None) -> dict[str, Any]:
        """Call the model and return the parsed JSON object from its reply.

        A *successful* call whose reply has no parseable JSON (reasoning model
        ran out of tokens, or emitted prose) is retried up to ``parse_attempts``
        times — a fresh generation usually parses. If ``validate`` is given, it
        is called with the parsed object; returning False means the response is
        semantically invalid (e.g. an out-of-set L0) and the query is RE-SENT,
        same as a parse failure. The last raw reply is attached to the raised
        LLMError (``.raw_content``) for the transcript.
        """
        last_content = ""
        for i in range(1, max(1, parse_attempts) + 1):
            resp = self.complete(messages, model=model, sleep=sleep)
            last_content = resp.content
            try:
                obj = extract_json(resp.content)
                if validate is not None and not validate(obj):
                    raise LLMError("response failed validation")
            except LLMError as ve:
                reason = str(ve) or "no JSON"
                LOG.warning("model=%s attempt=%d/%d rejected (%s, content_len=%d)",
                            model or self.model, i, parse_attempts, reason, len(resp.content or ""))
                if i < parse_attempts:
                    sleep(self._backoff(i))
                    continue
                err = LLMError(f"invalid response after {parse_attempts} generations ({reason}; "
                               f"last content_len={len(resp.content or '')})")
                err.raw_content = last_content
                raise err
            obj.setdefault("_latency_s", round(resp.latency_s, 3))
            obj.setdefault("_attempts", resp.attempts)
            obj.setdefault("_prompt_tokens", resp.prompt_tokens)
            obj.setdefault("_completion_tokens", resp.completion_tokens)
            obj.setdefault("_total_tokens", resp.total_tokens)
            obj.setdefault("_raw_content", resp.content)   # message.content string
            obj.setdefault("_raw_response", resp.raw)      # COMPLETE API response JSON (incl. reasoning_content, finish_reason, usage)
            return obj


def default_client() -> LLMClient:
    raise RuntimeError(
        "default_client() removed: LLMClient requires explicit base_url/api_key/model "
        "from iv_config.json 'gen' block — no built-in fallbacks."
    )
