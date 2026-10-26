# Proofread and repair desc_state (REPAIR phase)

You are in the **REPAIR** phase of the anything2task synthesis loop: given a current `desc_state` draft and
several pieces of "high-risk window" evidence, check whether the draft contains facts that are **missing, wrong, fabricated, or truncation-style placeholders**,
and output a **revised, complete `desc_state` JSON**.

## Inputs (mounted under `/app`; read them first)

1. `/app/input/desc_state.draft.json` — the current desc_state draft (contains several defects).
2. `/app/input/windows.md` — high-risk window evidence (decides which facts need correcting/reassigning).
3. `/app/input/repair_rules.md` — the complete repair rules and schema (**authoritative**).

## Repair principles (key points)

- **No truncation-style placeholders**: no field value may contain `...`, `…`, `省略`, `etc.`,
  `and so on`, `TODO`, `TBD`. If a fact is genuinely missing, write `unknown` (don't fabricate, and don't leave placeholders).
- **Don't delete existing reasonable facts**: focus on supplementing, clarifying, and reassigning.
- **When contradicting the evidence**, correct according to the later / more explicit window evidence.
- **Correct placement**: unresolved questions go into `open_questions`, deferred items go into `deferred_followups`;
  **neither** may be stuffed into `ambiguities`; only genuinely ambiguous items stay in `ambiguities`.
- Business facts (`长沙市旋极健康管理有限公司`, `旋极养生`, `员工手册七章`, the customer structure
  `中老年50%/青壮年30%/青少年20%`, `义工独立序列`, the 9-person team, the platform already adapted from
  `飞书` to Notion, etc.) must be preserved.

## Output

Write the revised **complete** desc_state to:

```
/app/output/desc_state.repaired.json
```

Requirements:

- It must be a valid JSON object whose structure **exactly matches** the schema in `repair_rules.md`
  (the four sections `task_overview` / `user_intent` / `workspace` / `reward` and all their sub-keys,
  with correct types: string fields are strings, list fields are lists, elements of `files_or_paths`/`data_files` are
  `{path, purpose}`, elements of `services_or_modules` are `{name, role}`);
- it contains **none** of the truncation-style placeholders above;
- missing facts are represented as `unknown`;
- deferred/unresolved items have been correctly placed; fabricated facts contradicting the evidence have been corrected; reasonable facts are preserved.
