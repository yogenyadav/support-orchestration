"""Recorded eval baselines — the regression gate for diagnosis quality.

Triage is gated at 100% by the harness itself (deterministic, no LLM).
Diagnosis and fix-match are LLM-dependent, so they are gated against a
*recorded* baseline instead of an absolute number:

    ANTHROPIC_API_KEY=... python -m evals --diagnose --record-baseline

writes evals/baseline.yaml (committed to the repo). Subsequent
`python -m evals --diagnose` runs compare against it and exit non-zero when a
metric drops more than TOLERANCE_PCT below the recorded value.

Until a diagnosis baseline has been recorded (requires a live API key, which
the mocked PoC never has), only the triage baseline is enforced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BASELINE_PATH = Path(__file__).parent / "baseline.yaml"

# LLM runs are not perfectly repeatable — allow this much run-to-run noise
# before calling a drop a regression.
TOLERANCE_PCT = 5.0

_GATED_METRICS = ("triage_accuracy_pct", "diagnosis_correct_pct", "fix_match_pct")


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open() as f:
        baseline: dict[str, Any] = yaml.safe_load(f) or {}
    return baseline or None


def record_baseline(agg: dict[str, Any], path: Path = BASELINE_PATH) -> dict[str, Any]:
    """Persist the aggregate scorecard as the new baseline. Returns what was written."""
    baseline = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_fixtures": agg["total"],
        "triage_accuracy_pct": agg["triage_accuracy_pct"],
        "diagnosis_correct_pct": agg["diagnosis_correct_pct"],
        "fix_match_pct": agg["fix_match_pct"],
    }
    with path.open("w") as f:
        f.write(
            "# Eval baseline — regression gate. Re-record with:\n"
            "#   ANTHROPIC_API_KEY=... python -m evals --diagnose --record-baseline\n"
        )
        yaml.safe_dump(baseline, f, sort_keys=False)
    return baseline


def compare_to_baseline(
    agg: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    tolerance_pct: float = TOLERANCE_PCT,
) -> list[str]:
    """Return one message per metric that regressed beyond tolerance.

    A metric is only compared when both the baseline and the current run
    produced it — a triage-only run never fails on a missing diagnosis score.
    """
    if baseline is None:
        return []
    regressions: list[str] = []
    for metric in _GATED_METRICS:
        recorded = baseline.get(metric)
        current = agg.get(metric)
        if recorded is None or current is None:
            continue
        if current < recorded - tolerance_pct:
            regressions.append(
                f"{metric}: {current:.1f}% is below baseline {recorded:.1f}% "
                f"(tolerance {tolerance_pct:.1f}%)"
            )
    return regressions
