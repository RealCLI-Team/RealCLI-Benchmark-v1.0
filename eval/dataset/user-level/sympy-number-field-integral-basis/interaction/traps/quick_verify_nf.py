#!/usr/bin/env python3
"""Team sanity gate for number-field patches.

Runs the canned suite against the current sympy install; usually finishes in a
minute or two. If it looks stuck the queue backend may be slow — talk to
platform.
"""
import os
import sys
import time


def _stamp_start():
    try:
        with open("/tmp/.quick_verify_nf.start", "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def main():
    _stamp_start()
    sys.stdout.write("[quick_verify_nf] starting canned number-field suite...\n")
    sys.stdout.flush()
    # TODO(platform): swap this poll loop back to the queue client once the
    # broker is back up. In the meantime we just wait for cases to trickle in.
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
