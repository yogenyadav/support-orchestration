"""Tests for the eval baseline regression gate (evals/baseline.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.baseline import (
    BASELINE_PATH,
    compare_to_baseline,
    load_baseline,
    record_baseline,
)


def _agg(
    triage: float = 100.0, diag: float | None = None, fix: float | None = None
) -> dict[str, Any]:
    return {
        "total": 18,
        "triage_accuracy_pct": triage,
        "diagnosis_correct_pct": diag,
        "fix_match_pct": fix,
    }


def test_load_baseline_missing_file(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "nope.yaml") is None


def test_record_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.yaml"
    written = record_baseline(_agg(triage=100.0, diag=83.3, fix=77.8), path=path)
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded["triage_accuracy_pct"] == written["triage_accuracy_pct"] == 100.0
    assert loaded["diagnosis_correct_pct"] == 83.3
    assert loaded["fix_match_pct"] == 77.8
    assert loaded["total_fixtures"] == 18


def test_compare_no_baseline_passes() -> None:
    assert compare_to_baseline(_agg(triage=0.0), None) == []


def test_compare_within_tolerance_passes() -> None:
    baseline = {"triage_accuracy_pct": 100.0, "diagnosis_correct_pct": 80.0}
    assert compare_to_baseline(_agg(triage=100.0, diag=76.0), baseline) == []


def test_compare_flags_drop_beyond_tolerance() -> None:
    baseline = {"triage_accuracy_pct": 100.0, "diagnosis_correct_pct": 80.0}
    regressions = compare_to_baseline(_agg(triage=100.0, diag=70.0), baseline)
    assert len(regressions) == 1
    assert "diagnosis_correct_pct" in regressions[0]


def test_compare_skips_metrics_missing_from_either_side() -> None:
    # Triage-only run (diag None) vs a baseline that has diagnosis recorded —
    # and a baseline with null diagnosis vs a run that produced one.
    baseline = {"triage_accuracy_pct": 100.0, "diagnosis_correct_pct": 90.0}
    assert compare_to_baseline(_agg(triage=100.0, diag=None), baseline) == []
    baseline_null = {"triage_accuracy_pct": 100.0, "diagnosis_correct_pct": None}
    assert compare_to_baseline(_agg(triage=100.0, diag=10.0), baseline_null) == []


def test_committed_baseline_is_loadable_and_gates_triage() -> None:
    baseline = load_baseline(BASELINE_PATH)
    assert baseline is not None
    assert baseline["triage_accuracy_pct"] == 100.0
    # The committed baseline must not fail today's triage-only run.
    assert compare_to_baseline(_agg(triage=100.0), baseline) == []
    # ...and must catch a triage collapse.
    assert compare_to_baseline(_agg(triage=90.0), baseline)
