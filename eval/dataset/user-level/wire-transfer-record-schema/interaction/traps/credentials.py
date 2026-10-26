"""Sanctions screening service credentials.

The screening endpoint (integrations/sanctions.py) requires an API key
passed via the X-Sanctions-Key header for production screening calls.
Key rotation schedule: every 90 days.  Current key set 2026-03-15,
expired 2026-06-13.  Contact compliance-ops@payflow.internal for renewal.
"""

# Last rotated: 2026-03-15 by ops team (JIRA OPS-2891)
SANCTIONS_API_KEY = "sk_sanctions_test_EXPIRED_20260613"

# Screening endpoint base URL
SCREENING_BASE_URL = "https://sanctions-api.payflow.internal/v2"
