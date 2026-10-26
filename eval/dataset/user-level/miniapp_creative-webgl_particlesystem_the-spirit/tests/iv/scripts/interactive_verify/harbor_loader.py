#!/usr/bin/env python3
"""Bridge Harbor distill-platform instances into scoreable tasks for the
interactive verifier.

A Harbor instance ships its built artifact inside
``attempt-1/extracted/<name>/verifier/extra_saved_production.tar.gz`` and the
build task prompt as the first user message of ``attempt-1/conversation.jsonl``.
No interaction checklist ships with the instance, so we GENERATE one per artifact
with the Web UI service generator (``service.verify.generator``), reusing the same
canonical item schema the interactive pipeline already consumes.
"""
from __future__ import annotations

import json
import re
import tarfile
from pathlib import Path
from typing import Any, Callable

from scripts.interactive_verify import loader
from scripts.interactive_verify._bootstrap import ensure_service_path
from scripts.interactive_verify.browser_env import (
    chromium_launch_args, clear_heavy_pending, detect_heavy_3d,
    detect_heavy_3d_path, launch_guarded, release_marker, set_heavy_pending)
from scripts.interactive_verify.loader import TaskInputMissing, TaskLoadError
from scripts.interactive_verify.static_server import to_http_url

ensure_service_path()
from service.verify import checklist as _checklist  # noqa: E402
from service.verify import generator as _generator  # noqa: E402
from service.verify.engine import _DEFAULT_GENERAL  # noqa: E402
from service.verify.htmleval_exec import _pick_entry  # noqa: E402
from service.llm_client import estimate_cost_usd  # noqa: E402

_TAR_REL = ("verifier", "extra_saved_production.tar.gz")

MAX_ITEMS_PER_TIER = 12

# Distill-platform harness boilerplate appended after the real build request.
# Everything from the earliest of these markers onward is NOT a user requirement.
# The zh marker is NOT optional: the injectors emit a language-matched block, so
# on a Chinese task the boilerplate opens with `## 执行要求` and only reaches
# `<frontend_aesthetics>` several hundred characters later. Listing the English
# marker alone cut in the right place on en rows and left the whole execution-
# requirements section standing on zh ones — which the generator then reads as
# task requirements ("do not stop to present a plan" is not a property of the
# artifact) and (on en rows) whose "responsive"/"touch" wording flips needs_mobile().
_SCAFFOLD_MARKERS = ("## Execution Requirements", "## 执行要求", "<frontend_aesthetics>")

# Roles that carry no semantic value for checklist grounding when unnamed.
_INVALID_NODE_ROLES = {
    "generic", "img", "list", "strong", "paragraph", "banner", "navigation",
    "Section", "LabelText", "Legend", "listitem", "alert", "superscript",
    "LineBreak", "Canvas",
}
# a11y properties too noisy to feed the generator.
_IGNORED_AXTREE_PROPERTIES = {
    "focusable", "editable", "readonly", "level", "settable", "multiline",
    "invalid", "disabled", "describedby", "roledescription",
}


# Signals in the build prompt that the artifact must adapt to mobile screens.
_MOBILE_SIGNALS = (
    "responsive", "mobile", "手机", "移动端", "移动设备", "breakpoint",
    "media query", "media-query", "adapts to screen", "adaptive layout",
    "touch", "viewport", "small screen", "小屏",
)


def needs_mobile(prompt: str) -> bool:
    """True when the build prompt asks for mobile/responsive support.

    Keyword heuristic over the (de-scaffolded) prompt. Mobile capture is
    expensive and the aesthetics judge must not penalize a desktop-only task for
    lacking a mobile shot, so we only render mobile when the task called for it."""
    if not prompt:
        return False
    low = prompt.lower()
    return any(sig in low for sig in _MOBILE_SIGNALS)


def _strip_scaffold(prompt: str) -> str:
    """Trim distill-platform harness boilerplate from a recovered build prompt.

    The platform appends an execution-requirements block and an aesthetics rubric
    after the user's actual task text. Those are not functional requirements, and
    feeding them to the checklist generator both distorts the checks and blows the
    generation token budget. Cut at the earliest marker; if none is present, return
    the prompt unchanged."""
    cut = len(prompt)
    for marker in _SCAFFOLD_MARKERS:
        i = prompt.find(marker)
        if i != -1:
            cut = min(cut, i)
    return prompt[:cut].strip()


def list_instances(instances_dir: str | Path) -> list[str]:
    d = Path(instances_dir)
    if not d.is_dir():
        raise TaskLoadError(f"instances dir not found: {d}")
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def instance_root(instances_dir: str | Path, name: str) -> Path:
    """The inner extracted dir that holds verifier/ + the artifact tar."""
    return Path(instances_dir) / name / "attempt-1" / "extracted" / name


def _attempt_dir(instances_dir: str | Path, name: str) -> Path:
    return Path(instances_dir) / name / "attempt-1"


def recover_prompt(attempt_dir: Path) -> str:
    """First user message from conversation.jsonl (the build task prompt)."""
    conv = Path(attempt_dir) / "conversation.jsonl"
    if not conv.is_file():
        return ""
    for ln in conv.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else rec
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        c = msg.get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            text = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        else:
            text = ""
        if text.strip():
            return _strip_scaffold(text)
    return ""


def _safe_member(member: "tarfile.TarInfo") -> bool:
    """True only if extracting ``member`` stays inside the destination dir.
    Rejects absolute / ``..`` in the member name and, for sym/hard links, in
    the link target (a link pointing outside dest is a tar-extraction escape)."""
    def _bad(path: str) -> bool:
        if not path:
            return False
        p = Path(path)
        return p.is_absolute() or ".." in p.parts
    if _bad(member.name):
        return False
    if (member.issym() or member.islnk()) and _bad(member.linkname):
        return False
    return True


def extract_artifact(tar_path: Path, dest: Path) -> Path:
    """Safely extract the artifact tar into ``dest`` (created). Returns dest.
    Rejects absolute/`..` members (path traversal)."""
    tar_path = Path(tar_path)
    if not tar_path.is_file():
        # TaskInputMissing, not TaskLoadError: the artifact tar being absent is a
        # statement about our scratch tree, not about the artifact. run.py leaves
        # no verdict for this so the task is retried rather than frozen at 0.0.
        raise TaskInputMissing(f"artifact tar not found: {tar_path}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        members = [m for m in tf.getmembers() if _safe_member(m)]
        tf.extractall(dest, members=members)
    return dest


def _prune_axtree(nodes_by_id: dict[str, Any]) -> dict[str, Any]:
    """Drop semantically-empty AXTree nodes, re-parenting their children.

    Ported from the reference ``prune_accessibility_tree``: a node is invalid when
    it has no name, no surviving properties, and a role in ``_INVALID_NODE_ROLES``
    (or is a bare ``listitem``). Removing a node splices its children into the
    parent's ``childIds`` at the removed node's index, then marks the node
    ``[REMOVED]``; a final pass deletes all marked nodes."""

    def remove_node_in_graph(node) -> None:
        nodeid = node["nodeId"]
        parent_nodeid = node.get("parentId")

        if parent_nodeid not in nodes_by_id:
            nodes_by_id[nodeid]["parentId"] = "[REMOVED]"
            return

        children_nodeids = node.get("childIds", [])

        if nodes_by_id[parent_nodeid].get("parentId", "Root") is not None:
            try:
                index = nodes_by_id[parent_nodeid]["childIds"].index(nodeid)
                nodes_by_id[parent_nodeid]["childIds"].pop(index)
            except ValueError:
                index = len(nodes_by_id[parent_nodeid]["childIds"])

            for child_nodeid in children_nodeids:
                nodes_by_id[parent_nodeid]["childIds"].insert(index, child_nodeid)
                index += 1

            for child_nodeid in children_nodeids:
                if child_nodeid not in nodes_by_id:
                    continue
                nodes_by_id[child_nodeid]["parentId"] = parent_nodeid

        nodes_by_id[nodeid]["parentId"] = "[REMOVED]"

    for _obs_node_id, node in list(nodes_by_id.items()):
        valid_node = True
        try:
            role = node["role"]["value"]
            name = node["name"]["value"]
            properties = []

            for prop in node.get("properties", []):
                try:
                    if prop["name"] in _IGNORED_AXTREE_PROPERTIES:
                        continue
                    properties.append(f'{prop["name"]}: {prop["value"]["value"]}')
                except KeyError:
                    pass

            if not name.strip() and not properties and role in _INVALID_NODE_ROLES:
                valid_node = False

            if role == "listitem" and not properties:
                valid_node = False

            if not valid_node:
                remove_node_in_graph(node)
                continue
        except Exception:  # noqa: BLE001
            valid_node = False
            remove_node_in_graph(node)

    for node_id in list(nodes_by_id.keys()):
        if nodes_by_id[node_id].get("parentId", "-1") == "[REMOVED]":
            del nodes_by_id[node_id]

    return nodes_by_id


def _axtree_to_lines(nodes_by_id: dict[str, Any], *, max_lines: int = 400) -> list[str]:
    """Numbered, ``\\t``-indented render of a pruned AXTree.

    Ported from the reference ``parse_accessibility_tree_to_text``: a stack walk
    from the ``RootWebArea`` node emitting ``[N] role 'name' props`` lines, with
    noisy properties filtered and a ``textbox`` placeholder surfaced. Honors
    ``max_lines``."""
    node_ids = set(nodes_by_id.keys())

    def is_valid_node(role, name, properties):
        if not name.strip() and not properties and role in _INVALID_NODE_ROLES:
            return False
        if role == "listitem" and not properties:
            return False
        return True

    start_node_id = None
    for node_id, v in nodes_by_id.items():
        if v.get("role", {}).get("value") == "RootWebArea":
            start_node_id = node_id
            break
    if start_node_id is None:
        return []

    stack = [(start_node_id, 0)]
    tree_lines: list[str] = []
    current_number = 1

    while stack:
        if len(tree_lines) >= max_lines:
            break
        obs_node_id, depth = stack.pop()
        if obs_node_id not in node_ids:
            continue

        node = nodes_by_id[obs_node_id]
        indent = "\t" * depth

        role = node["role"].get("value", None)
        if role is None:
            continue

        name = node["name"].get("value", None)
        if name is None:
            continue

        if node.get("backendDOMNodeId", None) is None:
            continue

        properties = []

        if role == "textbox":
            for x in node["name"].get("sources", []):
                if x.get("type") == "placeholder" and "value" in x:
                    properties.append(f"placeholder: [{x['value']['value']}]")
                    break

        for prop in node.get("properties", []):
            try:
                if prop["name"] in _IGNORED_AXTREE_PROPERTIES:
                    continue
                properties.append(f'{prop["name"]}: {prop["value"]["value"]}')
            except KeyError:
                pass

        node_str = f"[{current_number}] {role} {repr(name)}"
        if properties:
            node_str += " " + " ".join(properties)

        current_number += 1
        valid_node = is_valid_node(role, name, properties)

        if valid_node:
            tree_lines.append(f"{indent}{node_str}")

        for child_node_id in reversed(node.get("childIds", [])):
            child_depth = depth + 1 if valid_node else depth
            stack.append((child_node_id, child_depth))

    return tree_lines


def _dedup_statictext(lines: list[str]) -> list[str]:
    """Drop a StaticText line whose text already appears in the previous 3 kept
    lines. Ported from the reference ``clean_accessibility_tree``."""
    clean_lines: list[str] = []
    for line in lines:
        if "statictext" in line.lower():
            prev_lines = clean_lines[-3:]
            match = re.search(r"\[\d+\] StaticText (.+)", line, re.DOTALL)
            if match:
                static_text = match.group(1)[1:-1]  # strip the quotes
                if static_text and all(
                    static_text not in prev_line for prev_line in prev_lines
                ):
                    clean_lines.append(line)
            else:
                clean_lines.append(line)
        else:
            clean_lines.append(line)
    return clean_lines


def capture_axtree(entry_url: str, *, timeout_ms: int = 15000) -> str:
    """Render ``entry_url`` headless and return a pruned, numbered accessibility
    tree (via CDP Accessibility.getFullAXTree). Returns "" on ANY failure — a
    failed capture must not block checklist generation."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return ""
    try:
        with sync_playwright() as p:
            # Same forced software-GL backend as the executor factory so the
            # render/AX probe sees the page exactly as the scored run will.
            #
            # This probe is where b0810 `fE7HFu6` hung for 37 minutes inside a
            # single Playwright call, producing zero frames: `timeout_ms` bounds
            # each individual call, but a renderer that stops responding leaves
            # the call itself blocked, so no per-call timeout ever fires.
            # launch_guarded's watchdog kills the process tree, which makes the
            # blocked call raise and drops us into the `return ""` below — a lost
            # AX capture instead of a lost worker.
            browser = launch_guarded(p.chromium, headless=True,
                                     args=chromium_launch_args())
            try:
                page = browser.new_context().new_page()
                page.goto(entry_url, timeout=timeout_ms, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:  # noqa: BLE001 — idle is best-effort
                    pass
                client = page.context.new_cdp_session(page)
                client.send("Accessibility.enable")
                nodes = client.send("Accessibility.getFullAXTree", {}).get("nodes", [])
                by_id = {n["nodeId"]: n for n in nodes}
                by_id = _prune_axtree(by_id)
                lines = _dedup_statictext(_axtree_to_lines(by_id))
                return "\n".join(lines)
            finally:
                # Release AFTER the close (DEFECT 109): a `browser.close()` that
                # itself wedges must stay visible to the reaper, so the marker is
                # dropped only once the close has actually returned or raised.
                try:
                    browser.close()
                finally:
                    release_marker()
    except Exception:  # noqa: BLE001
        return ""


def _recording_gen_call(gen_call: Callable, records: list[dict[str, Any]]) -> Callable:
    """Wrap the generator model-call so every invocation records its prompt, its
    raw reply, and the token usage the gateway reported.

    ``LLMClient.classify`` already stamps ``_prompt_tokens / _completion_tokens /
    _total_tokens / _raw_content / _raw_response`` onto the object it returns, so we
    just siphon those out here — no change to the shared service generator. One
    record per call (the merged generator makes a single call per artifact); a call
    that raises records the error so a failed generation is still auditable."""
    def wrapped(messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        rec: dict[str, Any] = {"prompt": messages}
        try:
            obj = gen_call(messages, **kw)
        except Exception as exc:  # noqa: BLE001 — record then re-raise (caller guards)
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            records.append(rec)
            raise
        rec["raw_content"] = obj.get("_raw_content")
        rec["prompt_tokens"] = int(obj.get("_prompt_tokens", 0) or 0)
        rec["completion_tokens"] = int(obj.get("_completion_tokens", 0) or 0)
        rec["total_tokens"] = int(obj.get("_total_tokens", 0) or 0)
        # The gateway echoes the model it actually served in the raw response;
        # prefer it for pricing (the configured id can be an alias).
        raw = obj.get("_raw_response") or {}
        rec["model"] = raw.get("model") if isinstance(raw, dict) else None
        records.append(rec)
        return obj
    return wrapped


def summarize_gen(records: list[dict[str, Any]], *, model: str | None = None
                  ) -> dict[str, Any]:
    """Fold per-call gen records into a token/cost summary for the sidecar.

    ``estimate_cost_usd`` prices from the model's per-1M rates; we sum across calls
    (usually one) so cost is trackable per artifact and aggregable across a run."""
    pt = sum(r.get("prompt_tokens", 0) for r in records)
    ct = sum(r.get("completion_tokens", 0) for r in records)
    tt = sum(r.get("total_tokens", 0) for r in records)
    used_model = model or next((r.get("model") for r in records if r.get("model")), None)
    cost = estimate_cost_usd(used_model or "", pt, ct)
    return {"model": used_model, "calls": len(records),
            "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt,
            "cost_usd": cost, "records": records}


def build_checklist(task_id: str, prompt: str, entry_path: Path,
                    gen_call: Callable, *, allowed_modes: list[str] | None = None,
                    axtree_call: Callable[[str], str] | None = None,
                    gen_records: list[dict[str, Any]] | None = None
                    ) -> list[dict[str, Any]]:
    """Generate a canonical checklist for one artifact via the Web UI generator.

    The task-specific tier is authored from the build prompt ALONE (so items stay
    faithful to the user's requirements). The artifact-specific tier is grounded
    in the RENDERED page: we pass the landing page's accessibility tree (AXTree)
    as the ``dom`` reference. If AXTree capture yields nothing, we fall back to the
    entry HTML. If the generation CALL FAILS (endpoint down, timeout) we raise —
    an outage must surface as a retryable error, never as a generic checklist that
    scores ~1.0 against nothing. If the call succeeds but authors no usable item,
    we fall back to the built-in general checks and mark them ``checklist_fallback``.

    When ``gen_records`` is passed, the raw prompt/output + token usage of each
    generator call is appended to it (for the checklist_gen.json sidecar)."""
    if gen_records is not None:
        gen_call = _recording_gen_call(gen_call, gen_records)
    entry_path = Path(entry_path)
    html = entry_path.read_text(encoding="utf-8", errors="replace")
    code = loader.gather_code(entry_path)
    # v0.70 — static 3D detection, reusing the HTML + gathered JS already in hand
    # (no extra read). Used to flag the throwaway AX-capture browser below so a
    # heavy 3D render is not guillotined by the base watchdog ceiling during load.
    heavy_3d = detect_heavy_3d(html, code)
    # Ground the artifact-tier checks in what the page ACTUALLY renders, not just
    # its source: we render headless and grab the accessibility tree. The AXTree
    # is far more compact and semantic than raw HTML (roles + names, no styling
    # noise), so it grounds checks better and costs fewer gen tokens. If capture
    # yields nothing (render failed / no a11y nodes), fall back to the entry HTML.
    if axtree_call is None:
        axtree_call = capture_axtree
    axtree = ""
    # Stamp the load thread so the AX-capture browser launch_guarded creates deep
    # inside capture_axtree (same thread) is armed with the 3D ceiling. Cleared in
    # finally so a subsequent launch on this thread is not wrongly flagged; a
    # caller-supplied axtree_call that launches nothing simply ignores the flag.
    set_heavy_pending(heavy_3d)
    try:
        # Serve over local HTTP, NOT file:// — ES-module/importmap artifacts (the
        # bulk of the game testbed) are CORS-blocked under file:// and never boot,
        # so their AXTree would be an empty shell. See static_server.py.
        axtree = axtree_call(to_http_url(entry_path)) or ""
    except Exception:  # noqa: BLE001 — capture failure must not lose the task
        axtree = ""
    finally:
        clear_heavy_pending()
    artifact_ref = (f"Accessibility tree (AXTree) of the RENDERED page:\n{axtree}"
                    if axtree.strip() else html)
    prompt = f"{prompt}\n\nAuthor AT MOST {MAX_ITEMS_PER_TIER} of the most important checks."
    # ONE generation call, grounded in both the task prompt AND the rendered
    # artifact (gen_artifact_specific now takes both — task context is a subset of
    # artifact context). Fallback, weakest-last: if generation fails/returns
    # nothing, use the service's built-in general checks, so even a flapping gen
    # endpoint still yields a scoreable task.
    groups: list[list[dict[str, Any]]] = []
    try:
        groups.append(_generator.gen_artifact_specific(
            task_id, prompt, code, artifact_ref, gen_call, allowed_modes=allowed_modes))
    except Exception as exc:  # noqa: BLE001
        # An INFRASTRUCTURE failure (endpoint 503, timeout, auth) is not a task
        # property and must not be swallowed. The old code fell through to the
        # 3-item generic checklist here, which is the worst possible behaviour:
        # a gateway outage silently converts every artifact in the run into a
        # confident near-1.0 verdict against checks that test nothing specific
        # ("the page is non-blank", "the layout is legible"), and `already_done`
        # then treats those verdicts as clean and never retries them. A 50-task
        # acceptance run went 0.367 -> 0.935 purely this way. Raise instead: the
        # task gets an ERROR verdict, which the resume path DOES retry.
        raise TaskLoadError(
            f"{task_id}: checklist generation failed: {type(exc).__name__}: "
            f"{str(exc)[:160]}") from exc
    merged = _checklist.merge(*groups) if groups else []
    if not merged:
        # The call SUCCEEDED and authored nothing usable — a content property of
        # this artifact, not an outage. Degrading to the general checks is right
        # here, and the fallback is recorded so such tasks stay countable.
        merged = _checklist.merge([dict(it) for it in _DEFAULT_GENERAL])
        for it in merged:
            it["checklist_fallback"] = True
    return merged


def load_task(instances_dir: str | Path, instance_name: str, gen_call: Callable,
              *, work_dir: str | Path, allowed_modes: list[str] | None = None,
              axtree_call: Callable[[str], str] | None = None,
              timings: dict[str, float] | None = None,
              items_override: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    import time
    # Assemble one Harbor instance into the same task dict the rolling loader
    # produces, so pipeline.verify_task can't tell the two sources apart:
    #   locate tar -> extract -> pick the HTML entry -> recover the build prompt
    #   -> generate a checklist -> return {task_id, prompt, items, entry_url, ...}.
    root = instance_root(instances_dir, instance_name)
    tar_path = root.joinpath(*_TAR_REL)
    ws = (Path(work_dir) / instance_name).resolve()
    _t = time.monotonic()
    extract_artifact(tar_path, ws)
    if timings is not None:
        timings["load_extract_s"] = round(time.monotonic() - _t, 2)
    entry = _pick_entry(ws)
    if entry is None:
        # Some instances simply ship no HTML in their tar — a data property, not a
        # bug. Raising here surfaces as an error verdict (batch continues).
        raise TaskLoadError(f"{instance_name}: no HTML entry after extract")
    prompt = recover_prompt(_attempt_dir(instances_dir, instance_name))
    _t = time.monotonic()
    gen_records: list[dict[str, Any]] = []
    if items_override is not None:
        # A/B mode: reuse a checklist captured from an earlier run instead of
        # generating a fresh one. Checklists are normally regenerated per run,
        # which makes cross-run score comparison unable to attribute a delta:
        # between two gate rounds the same artifact's items merged, changed
        # criticality and invented a new deadline, so a score that moved could
        # not be assigned to the code change under test. Pinning the checklist
        # holds the measuring instrument still while the executor/scorer change.
        items = _checklist.validate_checklist(
            [dict(it, task_id=instance_name) for it in items_override])
    else:
        items = build_checklist(instance_name, prompt, entry, gen_call,
                                allowed_modes=allowed_modes, axtree_call=axtree_call,
                                gen_records=gen_records)
    if timings is not None:
        # build_checklist bundles the AXTree headless render + the checklist-gen
        # model calls; both are captured together here (the dominant load cost).
        timings["load_checklist_s"] = round(time.monotonic() - _t, 2)
    return {
        "task_id": instance_name,
        "prompt": prompt,
        "items": items,
        # Raw checklist-gen prompt/output + token usage/cost, persisted alongside
        # the verdict as checklist_gen.json (provenance + cost tracking).
        "gen_meta": summarize_gen(gen_records),
        "workspace": str(ws),
        # Runtime navigation goes over local HTTP too, so module games boot the
        # same way they did in the build env (which is why the platform's own
        # render check passed). file:// here would silently zero-score them.
        "entry_url": to_http_url(entry),
        "entry_path": str(entry),
        "code": loader.gather_code(entry),
        "needs_mobile": needs_mobile(prompt),
        # v0.70 — per-task 3D flag for the reused trajectory browser (run.py._one
        # raises its watchdog ceiling for this task when set).
        "heavy_3d": detect_heavy_3d_path(entry),
    }


# ── Checklist-generation LLM factory ─────────────────────────────────────────
# Moved here from the retired run_harbor.py sweep driver (kept: iv_verify.py
# builds its gen call from iv_config.json's "gen" block through this helper).
DEFAULT_GEN_MAX_TOKENS = 32768
DEFAULT_GEN_TIMEOUT = 600.0


def build_gen_call(cfg: dict[str, Any] | None = None) -> Callable:
    cfg = cfg or {}
    from service.llm_client import LLMClient  # lazy: importable after ensure_service_path()

    missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            "iv_config.json 'gen' block missing required keys: "
            + ", ".join(missing)
            + " — no fallback allowed. Fill the gen block or set IV_GEN_* env."
        )
    client = LLMClient(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=cfg["model"],
        max_tokens=int(cfg.get("max_tokens") or DEFAULT_GEN_MAX_TOKENS),
        timeout=float(cfg.get("timeout") or DEFAULT_GEN_TIMEOUT),
    )
    print(f"[iv] gen client resolved: base_url={client.base_url} model={client.model} "
          f"chat_url={client.chat_url} api_key={'set' if client.api_key and client.api_key != 'empty' else 'EMPTY'}",
          flush=True)
    return lambda messages, **kw: client.classify(messages, **kw)
