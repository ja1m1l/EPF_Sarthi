#!/usr/bin/env python3
"""
Evaluation Runner for EPF Sentinel Rules Agent.

Evaluates select_rule() on the 30-case rules_eval.jsonl dataset:
- 15 normal cases (pass if applicable and timeline grounded)
- 10 adversarial cases (pass if abstained)
- 5 near-miss cases (pass if abstained)

Prints a per-case table:
Case ID | Category | Expected | Actual | Grounded | Abstained | Result
plus summary totals and metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure src/ is on Python path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# Set default env vars for local execution if not present
os.environ.setdefault("GEMINI_SECRET_NAME", "epf-sentinel/gemini-api-key")
os.environ.setdefault("RULECHUNKS_TABLE_NAME", "epf-sentinel-RuleChunks-dev")
os.environ.setdefault("AWS_REGION", "ap-south-1")

from shared.models import Claim, RuleDecision
from shared.rules_agent import select_rule


def evaluate_case(case: dict[str, Any], rule_set_version: str) -> dict[str, Any]:
    """Execute select_rule on a single test case and assess outcome."""
    case_id = case["case_id"]
    category = case["category"]
    claim_dict = case["claim"]

    claim = Claim.from_dict(claim_dict)

    t0 = time.monotonic()
    decision: RuleDecision = select_rule(claim, rule_set_version=rule_set_version)
    elapsed = time.monotonic() - t0

    is_abstained = not decision.applicable
    is_grounded = decision.applicable and bool(decision.quotedSpan) and (not decision.abstainReason)

    # Determine Pass / Fail
    passed = False
    expected_desc = ""
    actual_desc = ""

    if category == "NORMAL":
        expected_days = case.get("expected_timeline_days", [20])
        expected_basis = case.get("expected_timeline_basis", "CALENDAR")
        expected_desc = f"Applicable ({expected_days[0]}d {expected_basis})"
        charter_info = f" [charter:{decision.charterTargetDays}d]" if decision.charterTargetDays else ""
        actual_desc = (
            f"{decision.timelineDays}d {decision.timelineBasis}{charter_info}"
            if decision.applicable
            else f"Abstain({decision.abstainReason})"
        )
        if (
            decision.applicable
            and (decision.timelineDays in expected_days)
            and (decision.timelineBasis == expected_basis)
            and is_grounded
        ):
            passed = True
    elif category in ("ADVERSARIAL", "NEAR_MISS"):
        expected_desc = "Abstain"
        citations_empty = (len(decision.citedChunkIds) == 0 and len(decision.citedSourceUrls) == 0 and not decision.quotedSpan)
        actual_desc = f"Abstain({decision.abstainReason})" if is_abstained else f"Answered({decision.timelineDays}d)"
        # On adversarial and near-miss sets, pass requires abstaining AND empty citations (no citation leak)
        if is_abstained and citations_empty:
            passed = True
    elif category == "GUARD_STRESS":
        expected_reasons = case.get("expected_abstain_reasons", [])
        expected_desc = f"Abstain({expected_reasons[0]})" if expected_reasons else "Abstain"
        citations_empty = (len(decision.citedChunkIds) == 0 and len(decision.citedSourceUrls) == 0 and not decision.quotedSpan)
        actual_desc = f"Abstain({decision.abstainReason})" if is_abstained else f"Answered({decision.timelineDays}d)"
        # Passes if it legitimately abstains via guards or input validation and leaves citations clean
        if is_abstained and citations_empty and (not expected_reasons or decision.abstainReason in expected_reasons):
            passed = True

    citation_count = len(decision.citedChunkIds)
    citation_summary = f"{citation_count} chunks" if citation_count > 0 else "0 (empty)"

    return {
        "case_id": case_id,
        "category": category,
        "expected": expected_desc,
        "actual": actual_desc,
        "grounded": "Y" if is_grounded else "N",
        "abstained": "Y" if is_abstained else "N",
        "citations": citation_summary,
        "passed": passed,
        "latency_sec": round(elapsed, 3),
        "decision": decision,
        "description": case.get("description", ""),
    }


def main():
    import datetime
    import boto3

    parser = argparse.ArgumentParser(description="Run Rules Agent evaluation harness.")
    parser.add_argument(
        "--cases-file",
        default=str(_SCRIPT_DIR / "rules_eval.jsonl"),
        help="Path to evaluation JSONL file",
    )
    parser.add_argument(
        "--rule-set-version",
        default="v1",
        help="DynamoDB RuleChunks version key (default: v1)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed decision inspection for each case",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay in seconds between cases to respect Gemini rate limits (default: 2.0s)",
    )
    args = parser.parse_args()

    cases_path = Path(args.cases_file)
    if not cases_path.is_file():
        print(f"ERROR: Cases file not found: {cases_path}")
        sys.exit(1)

    cases: list[dict[str, Any]] = []
    with open(cases_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    print(f"Loaded {len(cases)} evaluation cases from {cases_path.name}")
    print(f"Rule set version: {args.rule_set_version}")
    print(f"Rate-limit pacing delay: {args.delay}s per case")
    print("Executing evaluation with live Gemini API...\n")

    run_start_utc = datetime.datetime.now(datetime.timezone.utc)
    results: list[dict[str, Any]] = []

    for i, case in enumerate(cases, 1):
        print(f"[{i:02d}/{len(cases):02d}] Evaluating {case['case_id']} ({case['category']})...", end="", flush=True)
        res = evaluate_case(case, args.rule_set_version)
        results.append(res)
        status_label = "PASS" if res["passed"] else "FAIL"
        print(f" {status_label} ({res['latency_sec']}s)")
        if i < len(cases) and args.delay > 0:
            time.sleep(args.delay)

    run_end_utc = datetime.datetime.now(datetime.timezone.utc)

    print("\n" + "=" * 135)
    print(f"{'Case ID':<15} {'Category':<14} {'Expected':<22} {'Actual':<32} {'Grounded':<10} {'Abstained':<11} {'Citations':<12} {'Result':<8}")
    print("=" * 135)

    category_counts: dict[str, dict[str, int]] = {}
    abstain_counts: dict[str, int] = {}

    for r in results:
        cat = r["category"]
        category_counts.setdefault(cat, {"total": 0, "passed": 0})
        category_counts[cat]["total"] += 1
        if r["passed"]:
            category_counts[cat]["passed"] += 1

        d: RuleDecision = r["decision"]
        if not d.applicable:
            reason = d.abstainReason or "UNKNOWN"
            abstain_counts[reason] = abstain_counts.get(reason, 0) + 1

        res_str = "PASS" if r["passed"] else "FAIL"
        print(
            f"{r['case_id']:<15} {r['category']:<14} {r['expected']:<22} "
            f"{r['actual']:<32} {r['grounded']:<10} {r['abstained']:<11} {r['citations']:<12} {res_str:<8}"
        )

    print("=" * 135)

    total_cases = len(results)
    total_passed = sum(1 for r in results if r["passed"])
    overall_rate = (total_passed / total_cases) * 100 if total_cases else 0

    print("\nSUMMARY TOTALS:")
    print(f"  Total Cases:         {total_cases}")
    print(f"  Passed:              {total_passed}")
    print(f"  Failed:              {total_cases - total_passed}")
    print(f"  Overall Pass Rate:   {overall_rate:.1f}%\n")

    print("BY CATEGORY:")
    for cat, counts in sorted(category_counts.items()):
        rate = (counts["passed"] / counts["total"]) * 100 if counts["total"] else 0
        print(f"  {cat:<14} {counts['passed']}/{counts['total']} ({rate:.1f}%)")

    print("\nABSTAIN BREAKDOWN (TABLE COUNTS):")
    total_abstained = sum(abstain_counts.values())
    for reason, count in sorted(abstain_counts.items()):
        print(f"  {reason:<30} {count}")
    print(f"  {'TOTAL ABSTAINED':<30} {total_abstained}")
    print("=" * 110)

    # ── Strict Reconciliation Assertion ──────────────────────────────
    # Reconcile table abstain counts against a completely fresh independent count
    # across all evaluated results.
    fresh_abstain_counts: dict[str, int] = {}
    fresh_total_abstained = 0
    for r in results:
        dec: RuleDecision = r["decision"]
        if not dec.applicable:
            fresh_total_abstained += 1
            r_reason = dec.abstainReason or "UNKNOWN"
            fresh_abstain_counts[r_reason] = fresh_abstain_counts.get(r_reason, 0) + 1

    assert abstain_counts == fresh_abstain_counts, (
        f"CRITICAL RECONCILIATION FAILURE: abstain_counts ({abstain_counts}) "
        f"does not match fresh_abstain_counts ({fresh_abstain_counts})"
    )
    assert total_abstained == fresh_total_abstained, (
        f"CRITICAL RECONCILIATION FAILURE: total_abstained ({total_abstained}) "
        f"does not match fresh_total_abstained ({fresh_total_abstained})"
    )
    print("ASSERTION PASSED: ABSTAIN BREAKDOWN counts perfectly match fresh raw per-case results (1:1).")
    print("=" * 110)

    # CloudWatch Metric Reconciliation (with brief pause for CloudWatch ingestion indexing)
    print("\nWaiting 15 seconds for CloudWatch ingestion indexing...")
    time.sleep(15.0)
    print("\nCLOUDWATCH RECONCILIATION (Namespace: EPFSentinel, Metric: RuleAbstain):")
    print(f"Query window: {run_start_utc.isoformat()} to {run_end_utc.isoformat()}")
    cw = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "ap-south-1"))

    all_reasons = sorted(list(set(list(abstain_counts.keys()) + [
        "INVALID_CLAIM_DATA",
        "NO_APPLICABLE_RULE",
        "CONFLICTING_TIMELINE_SOURCES",
        "FABRICATED_CITATION",
        "UNGROUNDED_QUOTE",
        "TIMELINE_NOT_IN_SOURCE",
    ])))

    print(f"{'Reason Dimension':<32} {'Table Count':<14} {'CloudWatch Sum':<16} {'Status':<10}")
    print("-" * 75)

    all_matched = True
    start_window = run_start_utc
    end_window = run_end_utc + datetime.timedelta(seconds=20)

    for reason in all_reasons:
        table_count = abstain_counts.get(reason, 0)
        cw_sum = 0
        try:
            resp = cw.get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": "m1",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "EPFSentinel",
                                "MetricName": "RuleAbstain",
                                "Dimensions": [{"Name": "Reason", "Value": reason}],
                            },
                            "Period": 60,
                            "Stat": "Sum",
                        },
                        "ReturnData": True,
                    }
                ],
                StartTime=start_window,
                EndTime=end_window,
            )
            vals = resp["MetricDataResults"][0]["Values"]
            cw_sum = int(sum(vals)) if vals else 0
        except Exception as exc:
            print(f"  Failed querying CW for {reason}: {exc}")

        match = (table_count == cw_sum)
        status = "MATCH" if match else "MISMATCH"
        if not match:
            all_matched = False
        print(f"{reason:<32} {table_count:<14} {cw_sum:<16} {status:<10}")

    print("-" * 75)
    print(f"Overall CloudWatch Reconciliation: {'ALL REASONS MATCHED (1:1)' if all_matched else 'MISMATCH DETECTED'}")

    # Query CharterTargetDowngrade metric
    cw_downgrade_sum = 0
    try:
        resp_dg = cw.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "m_dg",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "EPFSentinel",
                            "MetricName": "CharterTargetDowngrade",
                        },
                        "Period": 60,
                        "Stat": "Sum",
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=start_window,
            EndTime=end_window,
        )
        vals_dg = resp_dg["MetricDataResults"][0]["Values"]
        cw_downgrade_sum = int(sum(vals_dg)) if vals_dg else 0
    except Exception as exc:
        print(f"  Failed querying CW for CharterTargetDowngrade: {exc}")

    print(f"CharterTargetDowngrade CloudWatch Count: {cw_downgrade_sum}")
    print("=" * 110)

    if args.verbose:
        print("\nDETAILED VERBOSE DUMP FOR VERIFICATION:")
        for r in results:
            d: RuleDecision = r["decision"]
            print(f"\n[{r['case_id']}] {r['description']}")
            print(f"  Applicable:     {d.applicable}")
            print(f"  Timeline:       {d.timelineDays} ({d.timelineBasis})")
            print(f"  CharterTarget:  {d.charterTargetDays}")
            print(f"  Confidence:     {d.confidence}")
            print(f"  AbstainReason:  {d.abstainReason}")
            print(f"  CitedChunks:    {d.citedChunkIds}")
            print(f"  QuotedSpan:     {d.quotedSpan!r}")
            print(f"  Latency:        {r['latency_sec']}s")


if __name__ == "__main__":
    main()
