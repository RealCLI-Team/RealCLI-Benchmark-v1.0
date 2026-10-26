# Deterministic Literature Relevance Scoring Engine

A systematic-review project has exported its candidate papers into several JSONL
batches and has provided an explicit scoring rulebook (a *rubric*). Your job is to
implement a command-line tool that reads the rubric, scores every paper in every
batch by a fully deterministic set of rules, derives a subtopic label, deduplicates,
sorts, and writes one merged structured result file.

There is **no subjective judgement and no network access**. The score of every paper
is completely determined by `rubric.json` plus the paper text.

## Input package

All inputs live under `/app/inputs`:

- `/app/inputs/rubric.json` — the scoring rulebook with these keys:
  - `base_score` (float), `min_score` (float), `max_score` (float)
  - `subtopic_score_threshold` (float)
  - `default_subtopic` (string)
  - `keywords`: a list of objects `{ "term": string, "category": string, "weight": float }`.
    `category` is one of `"business"`, `"project"`, `"ai_method"`, `"domain"`.
  - `subtopics`: an **ordered** list of objects `{ "label": string, "terms": [string, ...] }`.
- `/app/inputs/papers/` — a directory of batch files. Each `*.jsonl` file has one
  JSON object per line. Objects may contain `doi`, `title`, `abstract`, `year`.
  Files whose name does not end in `.jsonl` (for example `notes.txt`) must be ignored.

## Processing rules

Process **every** record from **all** `*.jsonl` files under `/app/inputs/papers/`.
Classification is by record content, never by file name. Establish a stable input
order by reading the `.jsonl` files sorted by file name (ascending) and, within a
file, by line number (ascending, 1-based).

### 1. Missing-abstract handling

The `abstract` is considered missing when it is `null`, the key is absent, or the
string is empty/whitespace-only. In that case set `abstract_missing` to `true` and
use only the title for matching. Otherwise `abstract_missing` is `false`.

### 2. Invalid records

- If `doi` is missing or empty/whitespace-only → `status="error"`, `reason="missing_doi"`.
- Otherwise, if `title` is missing or empty/whitespace-only → `status="error"`,
  `reason="missing_title"`.

`doi` is checked before `title`. Every error record is emitted with
`score=null`, `subtopic=""`, `matched_categories=[]`, `abstract_missing=false`.
Error records do **not** participate in deduplication or scoring.

### 3. Text matching

For a valid record build `text = lower(title + "\n" + abstract_used)`, where
`abstract_used` is the abstract string when present and non-blank, otherwise the empty
string. Each `keyword.term` and each subtopic `term` is matched by case-insensitive
substring test against `text`, and each distinct term is counted at most once.

### 4. Score

```
raw   = base_score + Σ (weight of each matched keyword term)
score = clamp(raw, min_score, max_score)
```

Round `score` to **1 decimal place using round-half-up** (for example `4.95 → 5.0`,
`8.45 → 8.5`). Valid records always have a numeric `score`.

### 5. matched_categories

The set of `category` values of the matched keywords, deduplicated and sorted in
**ascending lexicographic order**.

### 6. Subtopic label

- If `score < subtopic_score_threshold` → `subtopic=""`.
- If `score >= subtopic_score_threshold`: for each subtopic count how many of its
  `terms` appear in `text`. Choose the subtopic with the highest count; ties are broken
  by the earlier position in the `subtopics` list. If no subtopic term matches at all,
  use `default_subtopic`.

### 7. Deduplication

Deduplicate valid records by exact `doi`. When several valid records share a `doi`,
keep the one whose whitespace-stripped `abstract` string is **longest**; if the
stripped lengths are equal, keep the one that comes earlier in the input order
(file name ascending, then line number ascending). The kept record is scored and
emitted from its own fields.

### 8. Ordering of the output

- Valid (deduplicated) records come **first**, sorted by `score` descending, then by
  `doi` ascending.
- Error records come **after** all valid records, in input order (file name ascending,
  then line number ascending).

## Output

Write the result to the path given by `--output` as JSONL: one JSON object per line,
UTF-8, non-ASCII characters not escaped. Each object has exactly these keys:

```
doi, score, subtopic, matched_categories, abstract_missing, status, reason
```

For valid records `status="ok"` and `reason=""`.

## How it is run

```
python solution.py --input /app/inputs --output /app/result.jsonl
```

Your program must be a general rule engine driven entirely by `rubric.json`; it must not
hard-code answers for this particular batch.
