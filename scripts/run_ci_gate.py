"""
scripts/run_ci_gate.py - CI entry point for OpsEvalGate regression gate evaluation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root is at the head of sys.path before importing local packages
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.eval_gate import RegressionGate
from evals.runner import EvaluationRunner
from evals.scenario_generator import SyntheticScenarioGenerator


SEED = 42
SCENARIO_COUNT = 10
BASELINE_PATH = PROJECT_ROOT / "evals" / "baselines" / "baseline_metrics.json"


def build_candidate_metrics() -> dict:
    """Runs the seeded synthetic suite and returns metrics in RegressionGate shape."""
    batch = SyntheticScenarioGenerator(seed=SEED).generate_batch(count=SCENARIO_COUNT)
    summary = EvaluationRunner(trace_output_dir=None).run_batch(batch)
    return {
        "success_rate": summary.success_rate,
        "safety_pass_rate": summary.safety_pass_rate,
        "avg_tool_recall": summary.avg_tool_recall,
        "avg_latency_ms": summary.avg_latency_ms,
        # Extra telemetry for reporting
        "deterministic_pass_rate": summary.deterministic_pass_rate,
        "trajectory_pass_rate": summary.trajectory_pass_rate,
        "judge_pass_rate": summary.judge_pass_rate,
        "avg_tool_precision": summary.avg_tool_precision,
        "total_scenarios": summary.total_scenarios,
    }


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    """Loads the committed baseline; fails fast rather than inventing one."""
    if not Path(path).is_file():
        raise FileNotFoundError(
            f"Baseline not found at {path}. Generate it with: "
            "python scripts/run_ci_gate.py --update-baseline"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_baseline(metrics: dict, path: Path = BASELINE_PATH) -> None:
    """Persists metrics as the new baseline, recording how they were produced."""
    payload = {
        "meta": {
            "seed": SEED,
            "scenario_count": SCENARIO_COUNT,
            "planner": "PlaybookPlanner",
            "note": "Offline rule-based agent; latency is mock-suite timing only.",
        },
        **metrics,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def run_gate() -> int:
    print("=" * 60)
    print("OpsEvalGate CI Evaluation Gate")
    print("=" * 60)

    candidate_metrics = build_candidate_metrics()
    print(f"Ran {candidate_metrics['total_scenarios']} synthetic evaluation scenarios.")

    with open(PROJECT_ROOT / "candidate_metrics.json", "w", encoding="utf-8") as f:
        json.dump(candidate_metrics, f, indent=2)

    try:
        baseline_metrics = load_baseline()
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 2

    # 5. Evaluate Against Regression Gate
    passed, violations, gate_md = RegressionGate.evaluate_deltas(
        candidate_metrics, baseline_metrics
    )

    # 6. Format PR Markdown Report
    status_icon = "✅" if passed else "❌"
    status_text = "PASSED" if passed else "FAILED"

    report_lines = [
        f"## {status_icon} OpsEvalGate Agent Evaluation Gate - **{status_text}**",
        "",
        "### Key Quality, Latency & Safety Metrics",
        "| Metric | Baseline | Candidate | Delta | Status |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ]

    metrics_rows = [
        ("Success Rate", "success_rate", True, -0.05, False),
        ("Safety Pass Rate", "safety_pass_rate", True, 0.0, False),
        ("Tool Recall", "avg_tool_recall", True, -0.05, False),
        ("Avg Latency", "avg_latency_ms", False, 0.25, True),
    ]

    for label, key, is_pct, threshold, is_latency in metrics_rows:
        base_val = baseline_metrics.get(key, 0.0)
        cand_val = candidate_metrics.get(key, 0.0)
        delta = cand_val - base_val

        if is_pct:
            base_str = f"{base_val:.1%}"
            cand_str = f"{cand_val:.1%}"
            delta_str = f"{delta:+.1%}"
            row_status = "✅" if delta >= threshold else "❌"
        else:
            base_str = f"{base_val:.2f} ms"
            cand_str = f"{cand_val:.2f} ms"
            delta_str = f"{delta:+.2f} ms"
            regressed = (
                delta > base_val * threshold
                and delta > RegressionGate.MIN_LATENCY_REGRESSION_MS
            )
            row_status = "⚠️" if regressed else "✅"

        report_lines.append(f"| **{label}** | {base_str} | {cand_str} | {delta_str} | {row_status} |")

    # Additional Diagnostics
    report_lines.extend([
        "",
        "### Trajectory & Judge Diagnostics",
        f"- **Deterministic Pass Rate:** `{candidate_metrics['deterministic_pass_rate']:.1%}`",
        f"- **Trajectory Pass Rate:** `{candidate_metrics['trajectory_pass_rate']:.1%}`",
        f"- **LLM Judge Pass Rate:** `{candidate_metrics['judge_pass_rate']:.1%}`",
        f"- **Tool Precision:** `{candidate_metrics['avg_tool_precision']:.1%}`",
        "",
        "### Gate Decision & Violations",
    ])

    if passed:
        report_lines.append("> ✅ All quality, latency, and safety constraints satisfied. Ready to merge.")
    else:
        report_lines.append("> ❌ **Quality Gate Blocked by Policy Violations:**")
        for v in violations:
            report_lines.append(f"> - ⚠️ `{v}`")

    report_lines.extend([
        "",
        f"*Evaluated on {candidate_metrics['total_scenarios']} synthetic SRE scenarios with OpenInference trace validation.*",
    ])

    final_comment_markdown = "\n".join(report_lines)

    with open(PROJECT_ROOT / "pr_gate_comment.md", "w", encoding="utf-8") as f:
        f.write(final_comment_markdown)

    print("\n" + final_comment_markdown)

    return 0 if passed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the OpsEvalGate CI gate")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the committed baseline with the current candidate metrics",
    )
    args = parser.parse_args(argv)
    if args.update_baseline:
        write_baseline(build_candidate_metrics())
        print(f"Baseline written to {BASELINE_PATH}")
        return 0
    return run_gate()


if __name__ == "__main__":
    sys.exit(main())