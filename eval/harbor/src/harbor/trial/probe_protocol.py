"""模拟器与judge共享的探针交付规则，不把舞台指示当作用户台词。"""

from __future__ import annotations

import re
from typing import Any


def normalize_message(text: str) -> str:
    return " ".join(
        text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})).split()
    ).lower()


def _effective_length(text: str) -> int:
    return sum(3 if "\u4e00" <= char <= "\u9fff" else 1 for char in text)


def requires_delivery(probe: dict[str, Any]) -> bool:
    stimulus = str(probe.get("stimulus") or "").strip()
    passive = probe.get("passive") is True or bool(
        re.match(r"^(no verbal stimulus|passive (environment )?trap)\b", stimulus, re.I)
    )
    return bool(stimulus) and not passive


def _quoted_speech(text: str) -> list[str]:
    # 单词内部的撇号不是引号，避免把 "Sim's ... agent's" 间的元叙述选作锚点。
    quoted = re.findall(r'(?<!\w)"([^"\n]+)"', text)
    quoted += re.findall(r"(?<!\w)'(.*?)(?<!\\)'(?!\w)", text, re.S)
    return [normalize_message(q) for q in quoted if _effective_length(q) >= 20]


def stimulus_anchors(stimulus: str) -> list[str]:
    quoted = _quoted_speech(stimulus)
    if quoted:
        return sorted(set(quoted), key=len, reverse=True)
    sentences = re.split(r"(?<=[.!?。！？])\s+|\n+", stimulus)
    stage = re.compile(
        r"^(?:the user|user|you are|after the agent|before the agent|"
        r"if the agent|when the agent|deliver|fire|present|simulator|"
        r"sim's role|grounding for you|no verbal stimulus)\b",
        re.I,
    )
    candidates = [
        normalize_message(s)
        for s in sentences
        if not stage.match(s.strip()) and _effective_length(s.strip()) >= 20
    ]
    return sorted(set(candidates), key=len, reverse=True)[:3]


def stimulus_anchor(stimulus: str) -> str:
    anchors = stimulus_anchors(stimulus)
    return anchors[0] if anchors else ""


def message_matches_probe(
    probe: dict[str, Any], message: str, *, allow_response: bool = False
) -> bool:
    if not requires_delivery(probe):
        return False
    normalized = normalize_message(message)
    anchors = stimulus_anchors(str(probe.get("stimulus") or ""))
    explicit = probe.get("stimulus_anchors") or []
    anchors.extend(
        normalize_message(a) for a in explicit if isinstance(a, str) and a.strip()
    )
    if any(anchor in normalized for anchor in anchors):
        return True
    if allow_response:
        # 合法反应分支也可完成探针，但只用于模拟器明确声明的同一probe，
        # 不凭通用的让步话语自动给其他probe记账。
        responses = _quoted_speech(str(probe.get("guidance") or ""))
        return any(_effective_length(a) >= 40 and a in normalized for a in responses)
    return False


def delivered_probe_ids(script: dict[str, Any], elog: list[dict[str, Any]]) -> set[str]:
    probes = script.get("probes") or {}
    fired: set[str] = set()
    for entry in elog:
        unverified = set(entry.get("probe_unverified") or [])
        for pid in entry.get("probe_ids") or []:
            if isinstance(pid, str) and pid in probes and pid not in unverified:
                fired.add(pid)
        for pid, probe in probes.items():
            if (
                isinstance(pid, str)
                and isinstance(probe, dict)
                and message_matches_probe(probe, str(entry.get("message") or ""))
            ):
                fired.add(pid)
    return fired


def cancelled_slice_ids(script: dict[str, Any], elog: list[dict[str, Any]]) -> set[str]:
    fired = delivered_probe_ids(script, elog)
    return {
        s["id"]
        for s in script.get("info_slices") or []
        if isinstance(s, dict) and s.get("cancelled_by_probe") in fired
    }


def unreleased_slice_ids(
    script: dict[str, Any], elog: list[dict[str, Any]]
) -> list[str]:
    released = {sid for e in elog for sid in e.get("released_slice_ids") or []}
    cancelled = cancelled_slice_ids(script, elog)
    return sorted(
        s["id"]
        for s in script.get("info_slices") or []
        if isinstance(s, dict)
        and isinstance(s.get("id"), str)
        and s["id"] not in released | cancelled
    )
