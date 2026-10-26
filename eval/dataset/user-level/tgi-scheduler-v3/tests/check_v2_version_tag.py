"""Find version_tag definitions outside v3 modules, ignoring Rust literals/comments."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def rust_tokens(source: str) -> list[str]:
    tokens = []
    i = 0
    while i < len(source):
        if source[i].isspace():
            i += 1
        elif source.startswith('//', i):
            end = source.find('\n', i + 2)
            i = len(source) if end < 0 else end + 1
        elif source.startswith('/*', i):
            depth = 1
            i += 2
            while i < len(source) and depth:
                if source.startswith('/*', i):
                    depth += 1
                    i += 2
                elif source.startswith('*/', i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        else:
            raw = re.match(r'(?:br|r)(#{0,255})"', source[i:])
            char = re.match(r"'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\n])'", source[i:])
            if raw:
                end_marker = '"' + raw.group(1)
                end = source.find(end_marker, i + raw.end())
                i = len(source) if end < 0 else end + len(end_marker)
            elif source[i] == '"':
                i += 1
                while i < len(source):
                    if source[i] == '\\':
                        i += 2
                    elif source[i] == '"':
                        i += 1
                        break
                    else:
                        i += 1
            elif char:
                i += char.end()
            else:
                word = re.match(r'(?:r#)?[A-Za-z_][A-Za-z_0-9]*', source[i:])
                if word:
                    tokens.append(word.group().removeprefix('r#'))
                    i += word.end()
                else:
                    tokens.append(source[i])
                    i += 1
    return tokens


def has_v2_definition(source: str) -> bool:
    tokens = rust_tokens(source)
    v3_scopes: list[bool] = []
    for i, token in enumerate(tokens):
        in_v3 = bool(v3_scopes and v3_scopes[-1])
        if token == '{':
            enters_v3 = i >= 2 and tokens[i - 2:i] == ['mod', 'v3']
            v3_scopes.append(in_v3 or enters_v3)
        elif token == '}':
            if v3_scopes:
                v3_scopes.pop()
        elif token == 'fn' and tokens[i + 1:i + 2] == ['version_tag'] and not in_v3:
            return True
    return False


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '/app/router/client/src')
    if not root.is_dir():
        print('CLIENT_SOURCE_MISSING')
        return 1
    for path in sorted(root.rglob('*.rs')):
        relative = path.relative_to(root)
        if 'v3' in relative.parts[:-1] or path.stem == 'v3':
            continue
        if has_v2_definition(path.read_text()):
            print('VERSION_TAG_ADDED_ON_V2')
            return 0
    print('V2_CLEAN')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
