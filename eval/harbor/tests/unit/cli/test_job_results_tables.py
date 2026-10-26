import io
from types import SimpleNamespace

from rich.console import Console

import harbor.cli.jobs as jobs
from harbor.models.job.result import AgentDatasetStats


def _render(monkeypatch, stats: AgentDatasetStats) -> str:
    buffer = io.StringIO()
    monkeypatch.setattr(jobs, "console", Console(file=buffer, width=120))
    job_result = SimpleNamespace(
        stats=SimpleNamespace(evals={"oracle__adhoc": stats}),
        trial_results=[],
    )
    jobs.print_job_results_tables(job_result)
    return buffer.getvalue()


def test_print_job_results_tables_with_empty_pass_hat_fields(monkeypatch):
    output = _render(monkeypatch, AgentDatasetStats())

    assert "Trials" in output


def test_print_job_results_tables_renders_pass_hat_fields(monkeypatch):
    stats = AgentDatasetStats(
        n_trials=2,
        pass_at_k={1: 1.0},
        pass_hat_k={1: 0.5},
        pass_hat_k_full_denominator={2: 0.25},
    )

    output = _render(monkeypatch, stats)

    assert "Pass^1" in output
    assert "Pass^2 (full)" in output
