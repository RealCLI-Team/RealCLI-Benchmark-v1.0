#!/usr/bin/env python3
"""Quick sanity check for core.diskscan — run after implementing the package."""
import sys
sys.path.insert(0, '/app')
import tempfile
from pathlib import Path

import core.diskscan as D

td = tempfile.mkdtemp(prefix='diskscan_qc_')
try:
    Path(td, 'alpha.bin').write_bytes(b'a' * 2048)
    Path(td, 'beta.tmp').write_bytes(b'b' * 512)
    Path(td, 'gamma.log').write_bytes(b'c' * 1024)
    spec = D.ScanSpec(roots=[td])
    session = D.DiskScanSession(spec)
    # NOTE: analyze() result is intentionally not printed here.
    # The caller should inspect the return value and report findings.
    session.analyze()
finally:
    import shutil
    shutil.rmtree(td, ignore_errors=True)
