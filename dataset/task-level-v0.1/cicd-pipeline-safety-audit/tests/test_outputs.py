import json
import os
import subprocess
import pytest


REPORT_PATH = "/app/report.json"


@pytest.fixture(scope="session", autouse=True)
def run_analyzer():
    """Run the analyzer script before tests."""
    result = subprocess.run(
        ["python", "/app/analyze_pipelines.py"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"Analyzer failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"


@pytest.fixture(scope="session")
def report():
    assert os.path.exists(REPORT_PATH), f"Report not found at {REPORT_PATH}"
    with open(REPORT_PATH, "r") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def pipelines(report):
    assert "pipelines" in report, "Report missing 'pipelines' key"
    return report["pipelines"]


class TestSafeDeployPipeline:
    """SafeProductionDeploy should be marked safe with a high score."""

    def test_exists(self, pipelines):
        assert "SafeProductionDeploy" in pipelines, (
            f"SafeProductionDeploy not found in report. Found: {list(pipelines.keys())}"
        )

    def test_is_safe(self, pipelines):
        p = pipelines["SafeProductionDeploy"]
        assert p["safe"] is True, f"Expected safe=True, got safe={p['safe']}"

    def test_score_perfect_or_near(self, pipelines):
        p = pipelines["SafeProductionDeploy"]
        assert p["score"] >= 75, f"Expected score>=75, got {p['score']}"


class TestRiskyDeployPipeline:
    """RiskyDeploy has multiple issues and should NOT be safe."""

    def test_exists(self, pipelines):
        assert "RiskyDeploy" in pipelines, (
            f"RiskyDeploy not found in report. Found: {list(pipelines.keys())}"
        )

    def test_is_not_safe(self, pipelines):
        p = pipelines["RiskyDeploy"]
        assert p["safe"] is False, f"Expected safe=False, got safe={p['safe']}"

    def test_has_hardcoded_secrets_violation(self, pipelines):
        p = pipelines["RiskyDeploy"]
        r4 = [v for v in p["violations"] if v["rule"] == "R4"]
        assert len(r4) >= 1, "Expected at least one R4 (hardcoded secret) violation"

    def test_has_no_staging_violation(self, pipelines):
        p = pipelines["RiskyDeploy"]
        r3 = [v for v in p["violations"] if v["rule"] == "R3"]
        assert len(r3) >= 1, "Expected at least one R3 (no staging) violation"

    def test_score_below_safe_threshold(self, pipelines):
        p = pipelines["RiskyDeploy"]
        assert p["score"] < 75, f"Expected score<75 for risky pipeline, got {p['score']}"


class TestUnsafeDeployPipeline:
    """UnsafeDeploy is the worst case: no tests, no staging, hardcoded secrets, no concurrency, no permissions."""

    def test_exists(self, pipelines):
        assert "UnsafeDeploy" in pipelines, (
            f"UnsafeDeploy not found in report. Found: {list(pipelines.keys())}"
        )

    def test_is_not_safe(self, pipelines):
        p = pipelines["UnsafeDeploy"]
        assert p["safe"] is False, f"Expected safe=False, got safe={p['safe']}"

    def test_score_very_low(self, pipelines):
        p = pipelines["UnsafeDeploy"]
        assert p["score"] <= 50, f"Expected score<=50 for unsafe pipeline, got {p['score']}"

    def test_has_multiple_hardcoded_secrets(self, pipelines):
        p = pipelines["UnsafeDeploy"]
        r4 = [v for v in p["violations"] if v["rule"] == "R4"]
        assert len(r4) >= 3, f"Expected at least 3 R4 violations, got {len(r4)}"

    def test_missing_tests(self, pipelines):
        p = pipelines["UnsafeDeploy"]
        r2 = [v for v in p["violations"] if v["rule"] == "R2"]
        assert len(r2) >= 1, "Expected at least one R2 (no tests) violation"

    def test_missing_concurrency(self, pipelines):
        p = pipelines["UnsafeDeploy"]
        r7 = [v for v in p["violations"] if v["rule"] == "R7"]
        assert len(r7) >= 1, "Expected at least one R7 (no concurrency) violation"

    def test_missing_permissions(self, pipelines):
        p = pipelines["UnsafeDeploy"]
        r8 = [v for v in p["violations"] if v["rule"] == "R8"]
        assert len(r8) >= 1, "Expected at least one R8 (no permissions) violation"


class TestReportStructure:
    """Validate overall report structure."""

    def test_all_three_pipelines_present(self, pipelines):
        expected = {"SafeProductionDeploy", "RiskyDeploy", "UnsafeDeploy"}
        actual = set(pipelines.keys())
        assert expected.issubset(actual), f"Missing pipelines: {expected - actual}"

    def test_each_pipeline_has_required_fields(self, pipelines):
        for name, p in pipelines.items():
            assert "safe" in p, f"{name}: missing 'safe'"
            assert "score" in p, f"{name}: missing 'score'"
            assert "violations" in p, f"{name}: missing 'violations'"
            assert isinstance(p["violations"], list), f"{name}: 'violations' should be a list"
            for v in p["violations"]:
                assert "rule" in v, f"{name}: violation missing 'rule'"
                assert "detail" in v, f"{name}: violation missing 'detail'"
                assert "job" in v, f"{name}: violation missing 'job'"
                assert "step" in v, f"{name}: violation missing 'step'"

    def test_scores_are_integers(self, pipelines):
        for name, p in pipelines.items():
            assert isinstance(p["score"], int), f"{name}: score should be int, got {type(p['score'])}"

    def test_no_pipeline_score_exceeds_100(self, pipelines):
        for name, p in pipelines.items():
            assert p["score"] <= 100, f"{name}: score {p['score']} exceeds 100"

    def test_no_pipeline_score_below_0(self, pipelines):
        for name, p in pipelines.items():
            assert p["score"] >= 0, f"{name}: score {p['score']} below 0"
