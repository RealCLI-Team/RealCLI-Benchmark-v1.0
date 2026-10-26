import json
import sys

from . import process_order


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        with open(argv[0], "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = json.load(sys.stdin)
    print(json.dumps(process_order(payload), sort_keys=True))


if __name__ == "__main__":
    main()
