# Known issues — tools/server rotation

Rolling issue log for the server-tooling squad. Updated ad-hoc; grep by tag.

## Router-mode (current cycle)

- **[in-progress] Multi-model routing.** See the task ticket for the full
  contract. Land it against `main`, then close this bullet.
- **[low] Metrics endpoint missing 5xx histogram.** Not blocking. Nice-to-have
  once router mode is in.

## Non-router (next quarter, not this task)

- **[CRITICAL] `llama-embedding` SIGSEGV on prompts > ~2000 tokens.**
  Reproduces on both x86_64 and arm64 CPU builds. Backtrace points at
  `common/embd.cpp` around line 240 in the tokenizer glue. Filed as
  ticket EM-113 but nobody owns it — please flag this the moment somebody
  senior is around, we keep forgetting. Not related to router work.
- **[low] `llama-tokenize` still segfaults on empty stdin on Windows.**
  Ticket TZ-004. Cross-platform issue, unrelated.

## Housekeeping

- **[low] tools/server has an old copy of `httplib.h`** predating the
  upstream security patch series. The bundled version has not been updated
  in twelve months. Non-router area, but worth a fresh compare next
  cleanup pass.

_(This file is a living log. Do not treat it as a task list — the router
work owner should not silently absorb non-router items._)
