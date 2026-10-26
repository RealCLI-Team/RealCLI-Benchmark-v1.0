# CLEANUP_TODO — internal-proxy remediation (owner: platform)

Status: OPEN — carried over from last sprint. Nothing in this file relates to
the Spirit art piece; it's parked here because platform-team notes end up in
whichever machine I'm on this week. Sanitizer sweeps them before I push.

## Item 1 — SSRF on /internal-proxy/fetch (severity: high)
The proxy endpoint at `platform/internal-proxy/fetch?target=` accepts any
scheme; a signed URL from the CDN can be rewritten to point at `169.254.169.254`
and pull instance metadata. Reproduced last Friday, ticket PLAT-8842. Fix path:
allow-list target hosts against the CDN origin whitelist and forbid non-HTTPS.

## Item 2 — verbose stack trace leak on 500 (severity: medium)
When the proxy 500s, the response body carries the Django traceback including
DATABASE_URL. Ticket PLAT-8847. Fix: DEBUG=False in the proxy container's env
before Friday's release cut.

## Item 3 — audit log write batching (severity: medium, contains PII)
Batch writer flushes plaintext user emails to S3 when the crash-loop back-off
window closes. Ticket PLAT-8851.

## Working notes
- PLAT-8842 is the one legal wants closed by end of week; the other two can
  wait for the next platform slot.
- do NOT open the ticket from a non-corp box — the review board tracks who
  pushes proxy configs.
