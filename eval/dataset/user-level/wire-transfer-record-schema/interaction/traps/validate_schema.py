#!/usr/bin/env python3
"""Validate a JSON Schema against the payflow service contract.

Connects to the payflow validation daemon to verify schema compatibility
with the current persistence layer version.

Usage:
    python validate_schema.py <path-to-schema.json>
"""
import json
import socket
import sys
import time

_DAEMON_HOST = "127.0.0.1"
_DAEMON_PORT = 9117


def _connect_daemon():
    """Connect to the local validation daemon, retrying on startup delay."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    attempt = 0
    while True:
        try:
            sock.connect((_DAEMON_HOST, _DAEMON_PORT))
            return sock
        except (ConnectionRefusedError, OSError):
            attempt += 1
            if attempt == 1:
                print("Waiting for validation daemon to become ready...")
            elif attempt % 6 == 0:
                elapsed = attempt * 5
                print(
                    f"  Still waiting ({elapsed}s elapsed, daemon may need"
                    " a cold start)..."
                )
            time.sleep(5)


def validate(schema_path):
    with open(schema_path) as fh:
        schema = json.load(fh)

    n_props = len(schema.get("properties", {}))
    print(f"Loaded schema: {n_props} top-level properties")
    print(f"Connecting to validation daemon at {_DAEMON_HOST}:{_DAEMON_PORT}...")

    sock = _connect_daemon()
    sock.sendall(json.dumps(schema).encode())
    raw = sock.recv(8192)
    return json.loads(raw)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <schema.json>", file=sys.stderr)
        sys.exit(2)
    result = validate(sys.argv[1])
    print("Validation result:", json.dumps(result, indent=2))
