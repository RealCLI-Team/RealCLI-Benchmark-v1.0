# Batch allowlist HTML sanitizer with report

A web service lets users upload short HTML fragments that are later rendered on public profile
pages. You must ship a batch sanitizer that makes each fragment safe to display while preserving
legitimate formatting, and emit a machine-readable report describing what happened to every file.

## Input

A directory of HTML fragments lives at `/app/inputs/pages`. Every `*.html` file is one fragment.
File names are arbitrary labels only. The set mixes clearly malicious fragments, fragments that
only *look* risky but are legitimate, fully benign fragments, and one empty (0-byte) file that is a
valid input.

## Command

Provide a program that is invoked as:

```
python solution.py --input /app/inputs/pages --output /app/result.json
```

The program rewrites each `.html` file **in place** with its sanitized version, and writes a report
to `/app/result.json`.

## Sanitization policy (allowlist model)

1. **Allowed tags** (case-insensitive) are exactly:

   ```
   html, head, body, title, meta, p, br, span, div, h1, h2, h3, h4, h5, h6, ul, ol, li,
   strong, em, b, i, u, a, img, table, thead, tbody, tr, td, th, code, pre, blockquote
   ```

2. **Unwrap non-allowlisted tags.** Any tag not in the allowlist is removed, but its inner text and
   its (already-sanitized) children are kept. The text of the document is never deleted by
   unwrapping.

3. **Attribute policy** for surviving allowed tags:
   - `a` keeps only `href`, `title`
   - `img` keeps only `src`, `alt`, `title`
   - `meta` keeps only `charset`, `name`, `content`
   - every other allowed tag keeps **no** attributes

   Any attribute not permitted for its tag is removed.

4. **Dangerous protocol blocking.** On any surviving `href` or `src` value, take the value, trim
   leading whitespace and control characters, and compare case-insensitively. If it begins with
   `javascript:`, `vbscript:`, or `data:text/html`, drop that attribute only (keep the element).
   Other schemes, including `data:image/...`, `http:`, `https:`, and `mailto:`, are kept.

5. **Text content is never altered.** Dangerous-looking substrings that appear inside text nodes
   (for example inside `<code>` or `<pre>`, or as plain text such as `javascript:` or the word
   `onclick`) must remain verbatim.

## Report (`/app/result.json`)

`result.json` is a JSON object with a single key `files` whose value is an array **sorted ascending
by file name**. Each element is:

```json
{
  "name": "<file name>",
  "status": "sanitized" | "unchanged" | "error",
  "removed_tags": <int>,
  "stripped_attributes": <int>,
  "blocked_urls": <int>
}
```

- `removed_tags`: number of tags that were unwrapped (non-allowlisted).
- `stripped_attributes`: number of attributes removed due to the per-tag attribute policy.
- `blocked_urls`: number of `href`/`src` attributes removed because of a blocked scheme.
- `status` is:
  - `"unchanged"` when nothing was changed (all three counts are 0). An empty file is `unchanged`.
  - `"sanitized"` when at least one tag, attribute, or url was changed.
  - `"error"` when the file cannot be read or parsed; in that case the file is left unmodified and
    all counts stay 0.

The report array must be deterministic: running the program again on the same input must produce a
byte-identical `result.json`.
