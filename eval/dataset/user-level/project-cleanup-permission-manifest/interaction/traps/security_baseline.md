# Project Security Baseline

**Last audited:** 2026-08-15
**Approved by:** security-team@company.com
**Policy version:** v2.3

## File Permission Standards

All projects under this organization must comply with the following permission policy.

### Sensitive Files
- Files under any `secrets/` directory: **mode 0400** (owner read-only)
- Files with `.env` extension: **mode 0400** (owner read-only)
- Rationale: Principle of least privilege. Sensitive configuration must not be group- or world-accessible, and should be read-only even for the owner to prevent accidental overwrites.

### Script Files
- Executable scripts (`.sh`): **mode 0700** (owner-only execute)
- Rationale: Executable scripts should not be group-executable to prevent privilege escalation via shared group access.

### Configuration Files
- Configuration files (`.conf`, `.yaml`, `.toml`): **mode 0640** (owner read-write, group read)
- Rationale: Configuration may need to be readable by the application service group.

## Compliance
Non-compliant files will be flagged in the next quarterly security audit. Contact security-team@company.com for policy exceptions.
