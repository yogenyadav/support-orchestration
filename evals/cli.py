"""CLI entry point for the eval harness.

Usage:
    python -m evals                        # triage only — instant, no API key needed
    python -m evals --domain WES           # filter to one domain
    python -m evals --validate-fixtures    # check adapter wiring — no API key needed
    python -m evals --diagnose             # LLM diagnosis eval (needs ANTHROPIC_API_KEY)
    python -m evals --verbose              # enable DEBUG logging to logs/evals.log + stderr
    run-evals                              # if installed via pyproject.toml console_scripts

Exit code: 0 if all checks pass, 1 on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

_LOGS_DIR = Path(__file__).parent.parent / "logs"


def _configure_logging(verbose: bool) -> None:
    if not verbose:
        return
    _LOGS_DIR.mkdir(exist_ok=True)
    log_file = _LOGS_DIR / "evals.log"
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _print_table(results: list[dict[str, Any]]) -> None:
    col = 48
    header = f"{'fixture_id':<{col}} {'triage':>7} {'diag':>7} {'fix':>7}  status"
    print(header)
    print("-" * (col + 30))
    for r in results:
        triage = "PASS" if r["triage_accuracy"] else "FAIL"
        if r["diagnosis_skipped"]:
            diag = fix = "skip"
        else:
            diag = "PASS" if r["diagnosis_correct"] else "FAIL"
            fix = "PASS" if r["fix_match"] else "FAIL"
        ok = r["triage_accuracy"] and (r["diagnosis_skipped"] or r["diagnosis_correct"])
        status = "ok" if ok else "REGRESSION"
        print(f"{r['fixture_id']:<{col}} {triage:>7} {diag:>7} {fix:>7}  {status}")


def _print_summary(agg: dict[str, Any]) -> None:
    print()
    print(f"Total fixtures:    {agg['total']}")
    print(f"Triage accuracy:   {agg['triage_accuracy_pct']:.1f}%")
    if agg["diagnosis_correct_pct"] is not None:
        print(f"Diagnosis correct: {agg['diagnosis_correct_pct']:.1f}%")
        print(f"Fix match:         {agg['fix_match_pct']:.1f}%")
    else:
        n = agg["diagnosis_skipped_count"]
        t = agg["total"]
        print(f"Diagnosis:         skipped ({n}/{t} — pass anthropic_client to run_all_evals())")
    if agg.get("by_domain"):
        print()
        print("By domain:")
        for dom, counts in sorted(agg["by_domain"].items()):
            print(
                f"  {dom:<16} total={counts['total']}"
                f"  triage_ok={counts['triage_correct']}"
                f"  diag_ok={counts['diagnosis_correct']}"
            )


def _any_regression(agg: dict[str, Any]) -> bool:
    """Regression = any triage failure. Diagnosis skips are not regressions."""
    return any(not r["triage_accuracy"] for r in agg["results"])


def _print_validation_table(results: list[dict[str, Any]]) -> None:
    col = 48
    tools = ["phoenix_resolve", "db_state_read", "history_search", "log_read"]
    short = ["phoenix", "db_state", "history", "log_read"]
    header = f"{'fixture_id':<{col}}" + "".join(f" {s:>9}" for s in short) + "  blocker?  status"
    print(header)
    print("-" * (col + len(short) * 10 + 18))
    for r in results:
        checks = r["checks"]
        if r["status"] == "no_mocked_responses":
            print(f"{r['fixture_id']:<{col}}  (no mocked_tool_responses — skipped)")
            continue
        cells = ""
        for t in tools:
            c = checks.get(t)
            if c is None:
                cells += f" {'—':>9}"
            else:
                cells += f" {'ok' if c['ok'] else 'FAIL':>9}"
        hs = checks.get("history_search", {})
        blocker = "✓" if hs.get("blocker_class_match") else (
            "✗" if "blocker_class_match" in hs else "—"
        )
        status = "ok" if r["all_ok"] else "FAIL"
        print(f"{r['fixture_id']:<{col}}{cells}  {blocker:>7}  {status}")


def _print_validation_summary(agg: dict[str, Any]) -> None:
    print()
    print(f"Total fixtures:          {agg['total']}")
    print(f"Passed:                  {agg['passed']}")
    if agg["failed"]:
        print(f"Failed:                  {agg['failed']}")
    if agg["no_mocked_responses"]:
        print(
            f"No mocked responses:     {agg['no_mocked_responses']}"
            "  (add mocked_tool_responses to enable)"
        )
    print(f"blocker_class verified:  {agg['blocker_class_verified']}/{agg['blocker_class_total']}")


async def _run_validate(domain: str | None) -> int:
    from evals.harness import validate_all_fixture_adapters  # noqa: PLC0415

    print("Validating fixture adapter wiring (no LLM)...\n")
    agg = await validate_all_fixture_adapters(domain=domain)
    _print_validation_table(agg["results"])
    _print_validation_summary(agg)
    if agg["failed"]:
        print(f"\nResult: FAIL — {agg['failed']} fixture(s) have adapter errors.")
        return 1
    print("\nResult: ok — all fixture adapters wired correctly.")
    return 0


async def _run(domain: str | None, diagnose: bool = False, record_baseline: bool = False) -> int:
    import os

    from evals.baseline import compare_to_baseline, load_baseline
    from evals.baseline import record_baseline as _record_baseline
    from evals.harness import (
        run_all_evals,  # noqa: PLC0415 — lazy import avoids import-time side effects
    )

    anthropic_client = None
    if diagnose:
        try:
            import anthropic
            anthropic_client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        except KeyError:
            print(
                "Error: --diagnose requires ANTHROPIC_API_KEY environment variable.",
                file=sys.stderr,
            )
            return 1
        except ImportError:
            print(
                "Error: --diagnose requires 'anthropic' package (pip install anthropic).",
                file=sys.stderr,
            )
            return 1

    agg = await run_all_evals(domain=domain, anthropic_client=anthropic_client)
    _print_table(agg["results"])
    _print_summary(agg)
    if _any_regression(agg):
        print("\nResult: REGRESSION — one or more triage checks failed.")
        return 1

    if record_baseline:
        if domain is not None:
            print("\nError: --record-baseline requires a full run (no --domain filter).",
                  file=sys.stderr)
            return 1
        written = _record_baseline(agg)
        print(f"\nBaseline recorded: {written}")
    elif domain is None:
        # Baseline gate only makes sense against the full fixture set.
        regressions = compare_to_baseline(agg, load_baseline())
        if regressions:
            print("\nResult: REGRESSION vs recorded baseline —")
            for msg in regressions:
                print(f"  {msg}")
            return 1

    print("\nResult: ok")
    return 0


def sync_main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the eval harness against all fixture files."
    )
    parser.add_argument("--domain", default=None, help="Filter to one domain (e.g. WES)")
    parser.add_argument(
        "--validate-fixtures",
        action="store_true",
        help="Validate fixture adapter wiring without LLM calls (no API key needed)",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Enable LLM diagnosis eval (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--record-baseline",
        action="store_true",
        help="Write this run's scores to evals/baseline.yaml as the new regression baseline",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging to logs/evals.log and stderr",
    )
    args = parser.parse_args()
    _configure_logging(args.verbose)
    if args.validate_fixtures:
        sys.exit(asyncio.run(_run_validate(domain=args.domain)))
    sys.exit(asyncio.run(_run(
        domain=args.domain, diagnose=args.diagnose, record_baseline=args.record_baseline,
    )))
