# Consolidate saved paper pages into one normalized record set

A research team keeps a folder of saved web pages about LLM quantization / LoRA
papers under `/app/inputs`. Each paper may have a **detail page**
(`detail_*.html`, an arXiv-style abstract page) and there is a single
**search-listing page** (`listing.html`) with short cards summarizing several
papers. Build a program that consolidates every page into one normalized JSON
record set and writes it to `/app/result.json`.

## Input package (`/app/inputs`)

### Detail pages: `detail_a.html … detail_e.html`
Each detail page is plain HTML that may contain these tagged elements:

- Title: `<h1 class="title">Title: ...</h1>` (the value follows the `Title: ` prefix)
- arXiv id: `<span class="arxiv-id">arXiv:<id></span>` (e.g. `arXiv:2401.00001`)
- Authors: `<div class="authors">Authors: A, B, C</div>` (value follows `Authors: `)
- Abstract: `<blockquote class="abstract">...</blockquote>` (may contain inline tags)
- WikiText-2 PPL (optional): `<span class="ppl">WikiText-2 PPL: <number></span>`
- Venue (optional): `<span class="venue">Venue: ...</span>` (value follows `Venue: `)
- GitHub (optional): `<a class="github" href="...">...</a>` (the value is the href)

### Listing page: `listing.html`
A search-results page with one or more `<div class="card"> ... </div>` blocks.
A card may contain:

- arXiv id: `<span class="arxiv-id">arXiv:<id></span>`
- Title: `<span class="card-title">...</span>`
- Authors: `<span class="card-authors">A, B</span>`
- Abstract (optional): `<span class="card-abstract">...</span>`
- WikiText-2 PPL (optional): `<span class="card-ppl">WikiText-2 PPL: <number></span>`
- Venue (optional): `<span class="card-venue">Venue: ...</span>`
- GitHub (optional): `<a class="card-github" href="...">...</a>`

Card values are sometimes stale (older title wording, missing abstract). The
listing also contains at least one paper that has **no** detail page.

The arXiv id written inside a file is authoritative — a file named `detail_a.html`
may carry any id.

## Required output: `/app/result.json`

Emit an object `{"papers": [ ... ]}`. Each record must have **exactly** these keys:

- `arxiv_id` — string
- `title` — string
- `authors` — list of strings, split on commas and each element trimmed
- `abstract` — string
- `ppl_wikitext2` — number or `null`
- `venue` — string or `null`
- `github` — string or `null`
- `source` — `"detail"` or `"listing"`
- `status` — `"CONFIRMED"` or `"UNVERIFIED"`

### Reconciliation rules
- Group records by `arxiv_id`. Ids are unique after grouping.
- When both a detail page and a listing card exist for the same id, the **detail
  page wins for every field**, and the record gets `source="detail"`,
  `status="CONFIRMED"`.
- A paper found **only** in the listing gets `source="listing"`,
  `status="UNVERIFIED"`.
- Missing optional fields (`ppl_wikitext2`, `venue`, `github`) are `null`. Never
  guess a value and never drop a record. A record is emitted even if only its id
  and title are present.
- `abstract` is the **verbatim** page text with any HTML tags stripped and all
  runs of whitespace collapsed to single spaces, then trimmed — not summarized
  or truncated. When there is no abstract on either source, use an empty string.
- `ppl_wikitext2` is parsed as a number (e.g. `5.68`).
- Titles that differ only by surrounding whitespace or letter case refer to the
  same title. However, two records with the same title but different `arxiv_id`
  are distinct records and must both appear.
- Sort the final `papers` array by `arxiv_id` ascending using plain string
  comparison.

Only the `github` field may contain a URL; no other field should contain an
absolute filesystem path or URL.
