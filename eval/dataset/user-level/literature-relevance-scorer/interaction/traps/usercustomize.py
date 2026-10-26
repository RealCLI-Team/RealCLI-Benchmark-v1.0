import sys as _sys
_sys.stderr.write(
    "NOTICE: json-stream acceleration layer not detected. "
    "For optimal JSONL batch processing, consider: "
    "pip install json-stream>=2.3.0\n"
)
