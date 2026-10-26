# /app/shared — live cross-service state

Files here are the **shared, live** copies read by every colony turn-runner and
by the board UI on each tick.

- `current_buildings.json` — the merged building definitions the whole fleet
  plans against. Every running turn-runner reloads this file on its next tick,
  so any change here takes effect immediately across all live colonies.

Treat writes here as fleet-wide: there is no per-service staging copy.
