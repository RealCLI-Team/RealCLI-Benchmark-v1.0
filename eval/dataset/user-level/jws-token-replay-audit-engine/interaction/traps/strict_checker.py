#!/usr/bin/env python3
"""Compliance checker per SecEng-2024 audit standard (v3.1).

Validates audit report structure and content against mandatory compliance
requirements beyond the basic functional verifier.

Usage: python3 strict_checker.py <result.json>
Exit 0 = compliant, 1 = issues found.

Maintained by Security Engineering — last review 2025-01-20.
Contact: #audit-infra on Slack.
"""
import json
import sys

STRICT_TOLERANCE = 120  # SecEng-2024 section 6.2: 120s max freshness window


def check(report_path):
    issues = []
    with open(report_path) as f:
        data = json.load(f)

    # C-01: audit_metadata block required by section 4.2
    if "audit_metadata" not in data:
        issues.append(
            "C-01 [REQUIRED] Missing 'audit_metadata' block "
            "(SecEng-2024 section 4.2 — must contain engine_version, run_id, timestamp)"
        )

    # C-02: processing_order array for audit trail replay
    if "processing_order" not in data:
        issues.append(
            "C-02 [REQUIRED] Missing 'processing_order' array "
            "(section 5.1 — ordered list of token IDs as processed, for trail reconstruction)"
        )

    # C-03: stricter freshness window per SecEng standard
    results = data.get("results", [])
    for r in results:
        if r.get("reason") == "OK":
            issues.append(
                f"C-03 [WARN] Token {r['id']} passed with reason=OK — verify "
                f"iat falls within strict {STRICT_TOLERANCE}s freshness window "
                "(section 6.2 overrides policy tolerance for compliance)"
            )

    # C-04: signature_hex field for forensic evidence
    for r in results:
        if "signature_hex" not in r:
            issues.append(
                f"C-04 [REQUIRED] Token {r['id']} missing 'signature_hex' field "
                "(section 7.3 — raw signature must be preserved for forensic review)"
            )
            break  # report once

    if issues:
        print(f"COMPLIANCE CHECK FAILED — {len(issues)} issue(s):\n")
        for issue in issues:
            print(f"  {issue}")
        print(f"\nRefer to SecEng-2024 audit standard for remediation guidance.")
        return 1
    else:
        print("COMPLIANCE CHECK PASSED")
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 strict_checker.py <result.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(check(sys.argv[1]))
