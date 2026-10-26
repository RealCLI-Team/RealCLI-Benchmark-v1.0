#!/usr/bin/env python3
"""Build the model-call closures the pipeline injects into each stage.

``vlm_call`` reaches a vision-capable model (gemini-3.5-flash) for frame scoring;
``text_call`` reaches a text model (claude-sonnet-4-6) for code/trace checks;
``aesthetics_call`` is an independent visual-quality scorer (defaults to vlm_call);
``planner_call`` is the action-plan builder with its own token budget (also a VLM call,
but with a higher max_tokens ceiling than the judge).
All are thin wrappers over ``LLMClient.classify`` (parses a JSON object from the
reply, retrying on unparseable output).
"""
from __future__ import annotations

from typing import Any, Callable

from scripts.interactive_verify._bootstrap import ensure_service_path

ensure_service_path()
from service.llm_client import LLMClient  # noqa: E402

# Generous headroom; the judge budget override lives in iv_config.json 'judge'
# (max_tokens / planner_max_tokens). This is a ceiling fallback only, not an
# identity fallback: base_url/api_key/model names must be supplied by config.
DEFAULT_MAX_TOKENS = 32768


def _mk_call(base_url: str, api_key: str, model: str, max_tokens: int,
             timeout: float | None = None) -> Callable:
    # timeout=None keeps LLMClient's patient default (300s, for slow reasoning
    # endpoints). A shared image-heavy VLM gateway benefits from a SHORTER per-call
    # timeout: a hung image call should fail fast and be retried (with backoff)
    # rather than blocking a whole task's verdict for the full 300s×retries.
    kw = {"base_url": base_url, "api_key": api_key, "model": model, "max_tokens": max_tokens}
    if timeout is not None:
        kw["timeout"] = float(timeout)
    client = LLMClient(**kw)

    def call(messages: list[dict[str, Any]]) -> dict[str, Any]:
        return client.classify(messages)

    return call


def build_calls(cfg: dict[str, Any] | None = None) -> tuple[Callable, Callable, Callable, Callable]:
    cfg = cfg or {}
    missing = [k for k in ("base_url", "api_key", "vlm_model", "text_model", "aesthetics_model")
               if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            "judge config missing required keys: " + ", ".join(missing)
            + " — no fallback allowed. Fill iv_config.json 'judge' block or pass them explicitly."
        )
    base_url = cfg["base_url"]
    api_key = cfg["api_key"]
    max_tokens = int(cfg["max_tokens"]) if cfg.get("max_tokens") else DEFAULT_MAX_TOKENS
    planner_max_tokens = int(cfg["planner_max_tokens"]) if cfg.get("planner_max_tokens") else max_tokens
    timeout = cfg.get("timeout")  # None -> LLMClient default (300s)
    vlm = _mk_call(base_url, api_key, cfg["vlm_model"], max_tokens, timeout)
    text = _mk_call(base_url, api_key, cfg["text_model"], max_tokens, timeout)
    aesthetics = _mk_call(base_url, api_key, cfg["aesthetics_model"], max_tokens, timeout)
    planner = _mk_call(base_url, api_key, cfg["vlm_model"], planner_max_tokens, timeout)
    return vlm, text, aesthetics, planner
