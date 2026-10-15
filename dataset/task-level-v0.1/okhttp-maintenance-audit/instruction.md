You are maintaining a real Java repository snapshot cloned from `https://github.com/square/okhttp.git` at commit `7a350986e3231a7b81cbfcc6e3c185327033422b`.

The only project input in the environment is:

`/app/input/okhttp-real-repo-src.tar.gz`

The archive contains a top-level `okhttp-real-repo/` directory. Extract it under `/app` so the project root is exactly `/app/okhttp-real-repo` and `/app/okhttp-real-repo/source-origin.json` exists; do not create `/app/okhttp-real-repo/okhttp-real-repo`. The snapshot contains selected real upstream source paths plus a broken Java maintenance-audit tool under `tools/maintenance-audit` and a broken CI script at `scripts/run-maintenance-audit.sh`.

Requirements:

1. Leave `/app/input/okhttp-real-repo-src.tar.gz` in place and do not modify or delete it.
2. Unpack the archive so `/app/okhttp-real-repo/source-origin.json` exists and still reports repository `square/okhttp` and commit `7a350986e3231a7b81cbfcc6e3c185327033422b`.
3. Keep the real upstream source paths intact; use them as the repo root for path validation.
4. Fix `tools/maintenance-audit` so `mvn test` passes from that directory.
5. Fix `scripts/run-maintenance-audit.sh` so it runs `mvn test`, fails when Maven tests fail, and never uses `-DskipTests` or `maven.test.skip`.
6. After tests pass, the script must generate the final report by invoking `org.terminalbench.realrepo.MaintenanceAuditCli` with equivalent input and output arguments. The exact shell syntax is not prescribed.
7. The script must write `/app/maintenance-artifacts/java-real-okhttp-maintenance-audit-report.json`.
8. The CLI `org.terminalbench.realrepo.MaintenanceAuditCli` must support `--repo-root`, `--metadata`, `--modules`, `--dependencies`, `--advisories`, `--config`, and `--output`.
9. Read `config/maintenance-policy.properties` and apply `activeStatuses`, `includeTypes`, `ignoredScopes`, `minCoverage`, `maxFlakyTests`, `maxCiMinutes`, `recentReleaseCutoff`, `requiredJava`, `severityGate`, and `cvssGate`.
10. Read `data/module-catalog.csv`; evaluate only modules whose status and type are allowed by policy.
11. Validate each evaluated module path relative to `--repo-root`; paths outside the root or missing on disk must add `MISSING_PATH`.
12. Read `data/dependency-lock.csv` and `data/vulnerability-advisories.csv`; ignore configured dependency scopes and match dependency versions against inclusive introduced and exclusive fixed version ranges.
13. Add `VULNERABLE_DEPENDENCY` only for advisory matches whose severity and CVSS meet policy gates.
14. Add `LOW_COVERAGE`, `FLAKY_TESTS`, `CI_BUDGET`, `OLD_RELEASE`, and `JAVA_VERSION` findings from the policy thresholds.
15. Use severity order `NONE < LOW < MEDIUM < HIGH < CRITICAL` when computing maximum severity. Severity per finding type: `MISSING_PATH` = `HIGH`, `FLAKY_TESTS` = `HIGH`, `LOW_COVERAGE` = `MEDIUM`, `CI_BUDGET` = `MEDIUM`, `JAVA_VERSION` = `MEDIUM`, `OLD_RELEASE` = `LOW`, and `VULNERABLE_DEPENDENCY` takes the advisory's own severity. The threshold checks are strict inequalities: `coverage < minCoverage`, `flakyTests > maxFlakyTests`, `ciMinutes > maxCiMinutes`, `lastRelease < recentReleaseCutoff`, `javaVersion < requiredJava`. An owner with zero blocked modules reports `highestSeverity` `NONE`.
16. A module is healthy only when it has no findings. A blocked module is any evaluated module with at least one finding.
17. Sort `healthyModules` and `blockedModules` alphabetically.
18. Sort issue names inside each module finding in this order: `MISSING_PATH`, `VULNERABLE_DEPENDENCY`, `LOW_COVERAGE`, `FLAKY_TESTS`, `CI_BUDGET`, `OLD_RELEASE`, `JAVA_VERSION`.
19. `dependencyFindings` must contain objects with exactly these fields in order: `module`, `coordinate`, `version`, `severity`, `advisory`, `fixedVersion`. Sort them by `module`, then `coordinate`, then `advisory`.
20. `moduleFindings` must contain objects with exactly these fields in order: `module`, `path`, `owner`, `issues`, `highestSeverity`. Sort them by `module`.
21. `summaryByOwner` must contain one object for every owner that has at least one evaluated module, including owners with zero blocked modules. Each object must contain exactly these fields in order: `owner`, `activeModules`, `blockedModules`, `highestSeverity`. Sort owner summaries by `owner`.
22. Top-level `highestSeverity` is the maximum severity across blocked module findings, or `NONE` if there are no blocked modules.
23. Emit JSON with exactly these top-level fields in this order: `repository`, `sourceCommit`, `evaluatedModules`, `healthyModules`, `blockedModules`, `highestSeverity`, `dependencyFindings`, `moduleFindings`, `summaryByOwner`.
24. The output must be data-driven; the verifier will run the CLI against a shadow catalog, dependency lock, advisory set, policy file, metadata file, and repo root.
25. The golden expected report referenced by `MaintenanceAuditorTest` (`tools/maintenance-audit/src/test/resources/expected-maintenance-report.json`) is not present in the workspace; it is injected by the verifier only at evaluation time, and the test skips its comparison until then. Compute the report from the data files (`data/*.csv`, `config/maintenance-policy.properties`, `source-origin.json`); copying a static report file does not work because the verifier re-checks the output against sentinel and shadow datasets.

When complete, `/app/maintenance-artifacts/java-real-okhttp-maintenance-audit-report.json` must contain the deterministic maintenance report for the provided real repository snapshot.
