#!/usr/bin/env bash
set -euo pipefail
cd /app

python3 - <<'PY'
import os, re, json, glob

INPUT = "/app/inputs"
OUT = "/app/result.json"

def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def norm_title(t):
    return re.sub(r"\s+", " ", t).strip()

def parse_ppl(s):
    m = re.search(r"WikiText-2 PPL:\s*([0-9]+(?:\.[0-9]+)?)", s)
    return float(m.group(1)) if m else None

def parse_detail(html):
    idm = re.search(r'class="arxiv-id">\s*arXiv:\s*([0-9]+\.[0-9]+)', html)
    if not idm:
        return None
    arxiv_id = idm.group(1)
    tm = re.search(r'<h1 class="title">\s*Title:\s*(.*?)</h1>', html, re.DOTALL)
    title = norm_title(strip_tags(tm.group(1))) if tm else ""
    am = re.search(r'<div class="authors">\s*Authors:\s*(.*?)</div>', html, re.DOTALL)
    authors = []
    if am:
        authors = [a.strip() for a in strip_tags(am.group(1)).split(",") if a.strip()]
    absm = re.search(r'<blockquote class="abstract">(.*?)</blockquote>', html, re.DOTALL)
    abstract = strip_tags(absm.group(1)) if absm else ""
    pplm = re.search(r'<span class="ppl">(.*?)</span>', html, re.DOTALL)
    ppl = parse_ppl(pplm.group(1)) if pplm else None
    vm = re.search(r'<span class="venue">\s*Venue:\s*(.*?)</span>', html, re.DOTALL)
    venue = strip_tags(vm.group(1)) if vm else None
    gm = re.search(r'<a class="github" href="([^"]+)"', html)
    github = gm.group(1) if gm else None
    return {
        "arxiv_id": arxiv_id, "title": title, "authors": authors,
        "abstract": abstract, "ppl_wikitext2": ppl, "venue": venue,
        "github": github, "source": "detail", "status": "CONFIRMED",
    }

def parse_cards(html):
    out = []
    for card in re.findall(r'<div class="card">(.*?)</div>', html, re.DOTALL):
        idm = re.search(r'class="arxiv-id">\s*arXiv:\s*([0-9]+\.[0-9]+)', card)
        if not idm:
            continue
        arxiv_id = idm.group(1)
        tm = re.search(r'<span class="card-title">(.*?)</span>', card, re.DOTALL)
        title = norm_title(strip_tags(tm.group(1))) if tm else ""
        am = re.search(r'<span class="card-authors">(.*?)</span>', card, re.DOTALL)
        authors = []
        if am:
            authors = [a.strip() for a in strip_tags(am.group(1)).split(",") if a.strip()]
        absm = re.search(r'<span class="card-abstract">(.*?)</span>', card, re.DOTALL)
        abstract = strip_tags(absm.group(1)) if absm else ""
        pplm = re.search(r'<span class="card-ppl">(.*?)</span>', card, re.DOTALL)
        ppl = parse_ppl(pplm.group(1)) if pplm else None
        vm = re.search(r'<span class="card-venue">\s*Venue:\s*(.*?)</span>', card, re.DOTALL)
        venue = strip_tags(vm.group(1)) if vm else None
        gm = re.search(r'<a class="card-github" href="([^"]+)"', card)
        github = gm.group(1) if gm else None
        out.append({
            "arxiv_id": arxiv_id, "title": title, "authors": authors,
            "abstract": abstract, "ppl_wikitext2": ppl, "venue": venue,
            "github": github, "source": "listing", "status": "UNVERIFIED",
        })
    return out

records = {}
for path in sorted(glob.glob(os.path.join(INPUT, "detail_*.html"))):
    with open(path, encoding="utf-8") as f:
        rec = parse_detail(f.read())
    if rec:
        records[rec["arxiv_id"]] = rec

listing_path = os.path.join(INPUT, "listing.html")
if os.path.exists(listing_path):
    with open(listing_path, encoding="utf-8") as f:
        for card in parse_cards(f.read()):
            if card["arxiv_id"] not in records:
                records[card["arxiv_id"]] = card

papers = [records[k] for k in sorted(records.keys())]
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"papers": papers}, f, ensure_ascii=False, indent=2)
print("wrote", OUT, "with", len(papers), "records")
PY
