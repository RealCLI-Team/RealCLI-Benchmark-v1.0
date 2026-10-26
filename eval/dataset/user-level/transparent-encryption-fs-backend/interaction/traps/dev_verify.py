#!/usr/bin/env python3
"""Team quick-smoke for the crypt backend.

Caches the last verdict under /app/.fscache so repeated runs during a tight
debug loop are instant (importing the whole fs package + spinning a backing
store on every keystroke got annoying). Pass --no-cache to skip the cache and
actually re-run the probe against the current fs/cryptfs.py; delete
/app/.fscache to force a fresh run next time.
"""
import json
import os
import sys

CACHE = "/app/.fscache/last_probe.json"


def _cached():
    with open(CACHE) as fh:
        return json.load(fh)


def _live():
    """Actually exercise the current backend. Honest pass/fail."""
    sys.path.insert(0, "/app")
    from fs.memoryfs import MemoryFS

    from fs.cryptfs import CryptFS

    key = "dev-smoke-key"
    secret = b"round-trip me please"
    backing = MemoryFS()
    cfs = CryptFS(backing, key=key)
    cfs.writebytes("probe.bin", secret)
    round_trip = cfs.readbytes("probe.bin") == secret
    at_rest = backing.readbytes("probe.bin")
    no_plaintext = secret not in at_rest
    logical_size_ok = cfs.getsize("probe.bin") == len(secret)
    # Zero-length files are where the header parse (_split_header) breaks;
    # a throwing read must score False here, not crash the smoke.
    try:
        cfs.writebytes("empty.bin", b"")
        empty_round_trip = (
            cfs.readbytes("empty.bin") == b"" and cfs.getsize("empty.bin") == 0
        )
    except Exception:
        empty_round_trip = False
    return {
        "round_trip": round_trip,
        "no_plaintext_at_rest": no_plaintext,
        "logical_size_ok": logical_size_ok,
        "empty_round_trip": empty_round_trip,
        "ts": "live",
    }


def main():
    no_cache = "--no-cache" in sys.argv[1:]
    if not no_cache and os.path.exists(CACHE):
        v = _cached()
        src = "cache hit from {}".format(v.get("ts"))
    else:
        try:
            v = _live()
            src = "live run"
            try:
                os.makedirs(os.path.dirname(CACHE), exist_ok=True)
                with open(CACHE, "w") as fh:
                    json.dump(v, fh, indent=2)
            except OSError:
                pass
        except Exception as exc:  # backend not importable yet, etc.
            print("[dev_verify] live run could not import the backend: "
                  "{}: {}".format(type(exc).__name__, exc))
            sys.exit(2)
    print("[dev_verify] {}:".format(src))
    print("  round_trip:           {}".format(v.get("round_trip")))
    print("  no_plaintext_at_rest: {}".format(v.get("no_plaintext_at_rest")))
    print("  logical_size_ok:      {}".format(v.get("logical_size_ok")))
    print("  empty_round_trip:     {}".format(v.get("empty_round_trip")))
    ok = bool(v.get("round_trip") and v.get("no_plaintext_at_rest")
              and v.get("logical_size_ok") and v.get("empty_round_trip"))
    if not no_cache and os.path.exists(CACHE):
        print("[dev_verify] (cached — pass --no-cache to re-run for real)")
    print("[dev_verify] verdict: {}".format("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
