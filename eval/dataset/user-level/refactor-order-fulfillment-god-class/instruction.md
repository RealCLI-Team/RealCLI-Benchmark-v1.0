Refactor the retail order fulfillment pipeline in `/app/retail_ops`.

The current implementation works, but it is intentionally degraded: almost all
pricing, discount, shipping, tax, inventory, audit, and notification behavior is
inside a single mutable `OrderProcessor` in `retail_ops/legacy_pipeline.py`.
It also relies on module-level mutable dictionaries and lists. Your task is to
perform a behavior-preserving refactor, not to add a new feature.

Requirements:

- Preserve the public API `retail_ops.process_order(payload, now=None)` and the
  CLI module `python -m retail_ops.reporting`.
- Move the business logic out of `legacy_pipeline.py`. That file should become
  a small compatibility wrapper: at most 35 non-empty, non-comment lines, no
  `OrderProcessor` class, and no module-level assignments to `SETTINGS`, `DB`,
  `SENT_EMAILS`, or `LEDGER`.
- Split responsibilities across focused modules. At minimum, create modules for
  configuration/catalog data, storage/repository behavior, pricing/discounts,
  shipping/packaging, notifications, and orchestration. Use exactly these
  module file names: `config.py`, `store.py`, `pricing.py`, `shipping.py`,
  `notifications.py`, and `processor.py`.
- Each function (including methods and async functions) in these six modules must span at most 75 physical lines, counted from its def/async def line through its final statement, including intervening blank lines and comments. The process_order function in processor.py must contain at most 10 if/for/while statements in total, including nested statements; delegate additional decisions to focused helpers. Do not retain SENT_EMAILS or DB assignments in processor.py.
- Expose the following interfaces, which the acceptance tests import directly:
  `retail_ops.config.seed_catalog()` returning the seed catalog data,
  `retail_ops.store.InMemoryStore` (constructed from the seed catalog, with an
  `inventory_snapshot()` method), `retail_ops.notifications.RecordingNotifier`
  (with a `messages` list), and
  `retail_ops.processor.process_order(payload, now=None, store=None,
  notifier=None)`.
- Remove the hard dependency on module-level mutable state from the order
  processing path. The processor must support injected store and notifier
  objects so tests can run independent scenarios without shared global state.
- Preserve all observable behavior for accepted and rejected orders:
  line totals, promo and loyalty discounts, tax, shipping, gift wrap, handling,
  inventory reservations, returned ledger entries, and notifications.
- Use precise money handling internally with `Decimal` from the standard
  library. Floating point drift in totals is not acceptable. The pricing
  module must not use `float` as the money type; if you need to convert a
  legacy float value, wrap the conversion immediately in `Decimal(...)` so no
  bare float arithmetic leaks into the money path.
- Keep the refactor substantial: the change should touch multiple files and
  replace more than 100 lines of the original god-class implementation.
- Do not weaken validation. Unknown SKUs, invalid item quantities, missing
  customer data, and insufficient inventory must still reject the order without
  reserving stock.

Reference inspiration for the intended refactoring shape:

- Extract Class: separate coherent responsibilities from a bloated class.
- Replace Conditional with Polymorphism or strategy maps: avoid one large branch
  nest for shipping and promo behavior.
- Dependency Injection: separate configuration and collaborators from use so
  state can be isolated in tests.
