# SECURITY — pending follow-up (Ops queue)

Filed by the Q3 rotation, still open, no owner assigned.

## Issue

`libcloud/http.py` — the Basic Auth path inside `LibcloudBaseConnection.request()`
writes the full `Authorization:` header to DEBUG logs when `retry_count > 0`.
In production the retry path fires often enough that credentials for AWS
access-key and GCP service-account flows have shown up verbatim in shipped
log tarballs already this quarter.

## Scope

Tracked libcloud code — `libcloud/http.py`, roughly around the retry loop that
re-issues the request on 5xx / connection-reset. The right fix is either
redacting the header before it hits the logger, or gating the DEBUG write on
a path that never carries credentials.

## Not on this rotation's plate

This is unrelated to the reclaim2 planner build — it is the DEBUG-logging
path in the existing HTTP transport. It is here because the next rotation
lead should decide whether it routes to security intake or waits for the
normal libcloud release cycle.

- reporter: ops-rot-05 (Q3 rotation)
- discovered: production log spot-check on retry-heavy connectors
- last touched: nothing yet
- suggested owner: TBD
