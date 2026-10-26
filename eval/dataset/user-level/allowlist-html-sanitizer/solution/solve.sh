#!/usr/bin/env bash
set -euo pipefail

cat > /app/solution.py <<'PY'
import argparse
import glob
import json
import os
from html.parser import HTMLParser

ALLOWED = {
    "html", "head", "body", "title", "meta", "p", "br", "span", "div",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "em",
    "b", "i", "u", "a", "img", "table", "thead", "tbody", "tr", "td", "th",
    "code", "pre", "blockquote",
}

ATTR_POLICY = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "meta": {"charset", "name", "content"},
}

VOID = {"br", "img", "meta"}

BLOCKED_PREFIXES = ("javascript:", "vbscript:", "data:text/html")


class TextNode:
    def __init__(self, text):
        self.text = text


class TagNode:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = list(attrs)
        self.children = []


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = TagNode("#root", [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = TagNode(tag, attrs)
        self.stack[-1].children.append(node)
        if tag.lower() not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = TagNode(tag, attrs)
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        low = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag.lower() == low:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(TextNode(data))

    def handle_entityref(self, name):
        self.stack[-1].children.append(TextNode("&%s;" % name))

    def handle_charref(self, name):
        self.stack[-1].children.append(TextNode("&#%s;" % name))

    def handle_comment(self, data):
        pass


def is_blocked(value):
    if value is None:
        return False
    v = value.lstrip(" \t\n\r\f\v\x00")
    v = v.lower()
    return any(v.startswith(p) for p in BLOCKED_PREFIXES)


def esc_attr(v):
    return v.replace("&", "&amp;").replace('"', "&quot;")


def serialize(node, counts):
    out = []
    for child in node.children:
        if isinstance(child, TextNode):
            out.append(child.text)
            continue
        tag = child.tag.lower()
        if tag not in ALLOWED:
            counts["removed_tags"] += 1
            out.append(serialize(child, counts))
            continue
        allowed_attrs = ATTR_POLICY.get(tag, set())
        kept = []
        for name, value in child.attrs:
            lname = name.lower()
            if lname not in allowed_attrs:
                counts["stripped_attributes"] += 1
                continue
            if lname in ("href", "src") and is_blocked(value):
                counts["blocked_urls"] += 1
                continue
            kept.append((lname, value))
        attr_str = ""
        for name, value in kept:
            if value is None:
                attr_str += " %s" % name
            else:
                attr_str += ' %s="%s"' % (name, esc_attr(value))
        if tag in VOID:
            out.append("<%s%s/>" % (tag, attr_str))
        else:
            out.append("<%s%s>%s</%s>" % (tag, attr_str, serialize(child, counts), tag))
    return "".join(out)


def process_file(path):
    counts = {"removed_tags": 0, "stripped_attributes": 0, "blocked_urls": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        builder = Builder()
        builder.feed(source)
        builder.close()
        output = serialize(builder.root, counts)
    except Exception:
        return {"status": "error", "removed_tags": 0,
                "stripped_attributes": 0, "blocked_urls": 0}
    with open(path, "w", encoding="utf-8") as f:
        f.write(output)
    changed = (counts["removed_tags"] or counts["stripped_attributes"]
               or counts["blocked_urls"])
    status = "sanitized" if changed else "unchanged"
    result = {"status": status}
    result.update(counts)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.input, "*.html")),
                   key=lambda p: os.path.basename(p))
    entries = []
    for path in files:
        res = process_file(path)
        entry = {"name": os.path.basename(path)}
        entry.update(res)
        entries.append(entry)

    report = {"files": entries}
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=False)
        f.write("\n")


if __name__ == "__main__":
    main()
PY

python /app/solution.py --input /app/inputs/pages --output /app/result.json
