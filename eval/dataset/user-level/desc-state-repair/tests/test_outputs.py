# Auto-generated test for the desc-state-repair instance.
# Property-based verification: a repair has many valid forms, so the ground truth
# (/tests/groundtruth.json, shipped with the verifier, NOT in /app) is the SPEC of
# objective constraints; any output satisfying every property passes.
import json
import os

GT_PATH = "/tests/groundtruth.json"


def _gt():
    with open(GT_PATH, encoding="utf-8") as f:
        return json.load(f)


GT = _gt()
OUT_PATH = GT["output_path"]

_TYPES = {"str": str, "list": list, "dict": dict}


def _out():
    with open(OUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _all_strings(x):
    """Recursively collect every string VALUE in the structure."""
    out = []
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, list):
        for i in x:
            out.extend(_all_strings(i))
    elif isinstance(x, dict):
        for v in x.values():
            out.extend(_all_strings(v))
    return out


# --- existence / validity --------------------------------------------------
def test_output_exists():
    assert os.path.exists(OUT_PATH), f"{OUT_PATH} does not exist"


def test_output_is_json_object():
    data = _out()
    assert isinstance(data, dict), "output must be a JSON object"


# --- schema conformance ----------------------------------------------------
def test_schema_structure():
    data = _out()
    schema = GT["required_schema"]
    for section, fields in schema.items():
        assert section in data, f"missing top-level section {section!r}"
        assert isinstance(data[section], dict), f"section {section!r} must be an object"
        for key, typ in fields.items():
            assert key in data[section], f"{section}.{key} missing"
            assert isinstance(data[section][key], _TYPES[typ]), (
                f"{section}.{key} must be {typ}, got {type(data[section][key]).__name__}"
            )


def test_list_of_object_fields():
    data = _out()
    for dotted, subkeys in GT["list_of_object_fields"].items():
        sec, key = dotted.split(".")
        for item in data[sec][key]:
            assert isinstance(item, dict), f"{dotted} items must be objects"
            for sk in subkeys:
                assert sk in item and isinstance(item[sk], str), (
                    f"{dotted} item must have string field {sk!r}: {item}"
                )


# --- no truncation placeholders -------------------------------------------
def test_no_truncation_placeholders():
    data = _out()
    ci = {"etc.", "and so on", "TODO", "TBD"}  # match case-insensitively
    for s in _all_strings(data):
        for ph in GT["forbidden_placeholders"]:
            present = (ph.lower() in s.lower()) if ph in ci else (ph in s)
            assert not present, f"forbidden placeholder {ph!r} found in value: {s!r}"


# --- targeted corrections --------------------------------------------------
def test_missing_facts_are_unknown():
    data = _out()
    exp = GT["expect"]
    # repair_rules.md requires writing `unknown` AND (within the field) noting the
    # missing dimension, so accept "unknown（...）" style values that start with it.
    final_status = data["task_overview"]["final_status"]
    assert isinstance(final_status, str) and final_status.startswith(exp["task_overview.final_status"]), (
        "final_status should be 'unknown' (evidence: unknown at iteration-1)"
    )
    ref_path = data["reward"]["reference_solution_path"]
    assert isinstance(ref_path, str) and ref_path.startswith(exp["reward.reference_solution_path"]), (
        "reward.reference_solution_path should be 'unknown' (no reference solution yet)"
    )


def test_items_routed_to_correct_buckets():
    data = _out()
    exp = GT["expect"]
    ui = data["user_intent"]

    # the deferred item's evidence wording differs from the draft by one character
    # ("先暂缓" vs "暂缓"); both are faithful, so match on its stable key content
    deferred_keys = ["员工手册英文版", "暂缓", "七章定稿"]
    assert any(all(k in item for k in deferred_keys) for item in ui["deferred_followups"]), (
        "deferred item not in deferred_followups"
    )
    assert exp["deferred_item"] not in ui["ambiguities"], "deferred item still in ambiguities"
    assert not any(all(k in item for k in deferred_keys) for item in ui["ambiguities"]), (
        "deferred item still in ambiguities"
    )
    # likewise, the open question and the kept ambiguity may be reworded in a
    # faithful repair, so match on their stable key content too
    open_q_keys = ["Business 套餐", "AI 集成额度", "管理员确认"]
    assert any(all(k in item for k in open_q_keys) for item in ui["open_questions"]), (
        "open question not in open_questions"
    )
    assert not any(all(k in item for k in open_q_keys) for item in ui["ambiguities"]), (
        "open question still in ambiguities"
    )
    kept_amb_keys = ["客户结构比例", "义工"]
    assert any(all(k in item for k in kept_amb_keys) for item in ui["ambiguities"]), (
        "genuine ambiguity must remain in ambiguities"
    )


def test_task_threads_preserved_and_clean():
    data = _out()
    threads = data["task_overview"]["task_threads"]
    for t in GT["expect"]["task_threads_include"]:
        # threads may be annotated/clarified; require the original thread text to survive
        assert any(t in thread for thread in threads), f"task_threads must still include {t!r}"


def test_services_use_notion_not_feishu():
    data = _out()
    exp = GT["expect"]
    names = [s["name"] for s in data["workspace"]["services_or_modules"]]
    assert any(exp["services_must_include_substr"] in n for n in names), (
        f"services_or_modules must include a {exp['services_must_include_substr']!r} entry"
    )
    for bad in exp["services_forbid_substr"]:
        assert not any(bad in n for n in names), (
            f"services_or_modules must not list a {bad!r} entry (contradicts evidence)"
        )


# --- don't delete reasonable facts ----------------------------------------
def test_preserved_facts_present():
    blob = "\n".join(_all_strings(_out()))
    for sub in GT["preserved_substrings"]:
        assert sub in blob, f"preserved business fact {sub!r} missing from output"
