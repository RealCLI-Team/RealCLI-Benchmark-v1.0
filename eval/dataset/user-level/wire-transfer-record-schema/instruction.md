The payment service in `/app` lacks documentation for one of its persistence
contracts. Read the codebase and determine the complete JSON object shape that
is written when a wire transfer request is accepted and persisted.

Create a file named `/app/transfer_record_schema.json` containing a JSON Schema
for that persisted record.

Requirements:

- Do not change the service code.
- Infer the schema from the actual request/event/command/persistence flow, not
  from a guessed example payload.
- The schema must describe every persisted top-level key and nested object used
  for the accepted wire transfer record.
- Include required fields, scalar JSON types, arrays, nested object shapes,
  enum values where the code restricts them, and `additionalProperties: false`
  for every object whose shape is fixed by the code.
- Use deterministic key ordering in the JSON file.
- The result must be valid JSON.

Hints:

- The answer is not documented in the README because this repository has no
  README.
- The event starts near the public request boundary, but the persisted shape is
  assembled deeper in the codebase after validation, risk classification,
  ledger entry construction, and audit enrichment.
- Use whole-repository search and code reading to trace the flow.
