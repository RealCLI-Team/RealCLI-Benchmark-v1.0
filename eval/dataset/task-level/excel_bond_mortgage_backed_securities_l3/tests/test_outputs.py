#!/usr/bin/env python3
"""Pytest verifier for an SSB-V2 Financial_Model Excel task.

Recomputes the agent output at /workspace/output.xlsx (LibreOffice headless) and scores the
answer region against the frozen reference (golden.xlsx) using the SSB official comparator.

reward = 1.0 iff every cell in the region matches (regression==1.0 AND modification==1.0),
1% numeric tolerance. regression / modification ratios are recorded as diagnostics.
Writes /logs/verifier/reward.txt and /logs/verifier/reward_components.json.
"""
import atexit, json, os, sys, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # vendored evaluation.py + open_spreadsheet.py

ANSWER_POSITION = "'furtherex2'!A33:K113"
OUTPUT   = "/workspace/output.xlsx"
GOLDEN   = str(HERE / "golden.xlsx")
INPUT    = "/workspace/data/input.xlsx"        # present in verifier stage; used for reg/mod split
LOG      = Path("/logs/verifier")

_components = {"regression": 0.0, "modification": 0.0, "accuracy": 0.0, "error": ""}

def _write_reward():
    LOG.mkdir(parents=True, exist_ok=True)
    try: json.dump(_components, open(LOG/"reward_components.json","w"), indent=2)
    except Exception: pass
    try: open(LOG/"reward.txt","w").write(str(_components.get("accuracy", 0.0)))
    except Exception: pass
atexit.register(_write_reward)

def _recalc_output():
    """LibreOffice-recompute /workspace/output.xlsx in place so openpyxl data_only sees values.

    The UNO bridge can be unavailable in the verifier image (python3-uno missing), in
    which case start_libreoffice_service never becomes ready and batch_open_files would
    silently skip the recompute, leaving formula cells without cached values. Fall back
    to a headless --convert-to round-trip, which recalculates formulas on save."""
    import open_spreadsheet as osp
    ok = False
    try:
        ok = osp.start_libreoffice_service()
        if ok:
            osp.batch_open_files([OUTPUT])
    except Exception:
        ok = False
    finally:
        try: osp.stop_libreoffice_service()
        except Exception: pass
    if not ok:
        _recalc_via_convert()

def _recalc_via_convert():
    """Recalculate via a soffice --convert-to round-trip (no UNO bridge required)."""
    import glob, shutil, subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", td, OUTPUT],
            capture_output=True, timeout=600)
        produced = glob.glob(os.path.join(td, "*.xlsx"))
        if produced:
            shutil.move(produced[0], OUTPUT)

def test_output_exists():
    # [iq0822 fix] 缺产物必须记 0,不能靠后续零分母回落成 1.0
    if not os.path.exists(OUTPUT):
        _components.update(regression=0.0, modification=0.0, accuracy=0.0,
                           error="output file not exist: /workspace/output.xlsx")
    assert os.path.exists(OUTPUT), "agent did not produce /workspace/output.xlsx"

def test_recalc():
    _recalc_output()
    assert os.path.exists(OUTPUT)

def test_accuracy():
    from evaluation import compare_workbooks_with_regression
    inp = INPUT if os.path.exists(INPUT) else str(HERE / "input_ref.xlsx")
    is_ok, err, corr, tot, reg, mod = compare_workbooks_with_regression(
        inp, GOLDEN, OUTPUT, ANSWER_POSITION)
    # [iq0822 fix] 零分母不再回落 1.0。modification 分母为 0 表示"没有任何该改的格子被比对",
    # 这在正常题目里不可能发生(答案区非空),出现即判分链异常 ⇒ 记 0 而非满分。
    reg_ratio = round(reg["correct"]/reg["total"], 4) if reg["total"] else 0.0
    mod_ratio = round(mod["correct"]/mod["total"], 4) if mod["total"] else 0.0
    if not mod["total"]:
        err = (err or "") + " | empty modification denominator (answer region not compared)"
    acc = 1.0 if (reg_ratio == 1.0 and mod_ratio == 1.0) else 0.0
    _components.update(regression=reg_ratio, modification=mod_ratio, accuracy=acc, error=err or "")
    assert acc == 1.0, (f"answer-region mismatch: reg={reg_ratio} mod={mod_ratio} "
                        f"({corr}/{tot} cells) first_error={err}")
