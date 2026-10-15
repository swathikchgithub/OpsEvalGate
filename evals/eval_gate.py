"""evals/eval_gate.py - CI/CD regression quality gate and delta comparison."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Tuple


class RegressionGate:
  """Enforces non-negotiable quality barriers between candidate and baseline metrics."""

  MAX_ALLOWED_SUCCESS_DROP = 0.02  # Max 2% accuracy regression
  MAX_ALLOWED_RECALL_DROP = 0.05  # Max 5% recall regression
  MAX_ALLOWED_LATENCY_INFLATION = 0.30  # Max 30% latency increase
  # Noise floor: ignore sub-5ms shifts, which are scheduler jitter on CI runners
  # rather than regressions (the mock suite's baseline is ~0.06ms).
  MIN_LATENCY_REGRESSION_MS = 5.0

  @classmethod
  def evaluate_deltas(
      cls, candidate: Dict[str, Any], baseline: Dict[str, Any]
  ) -> Tuple[bool, List[str], str]:
    violations: List[str] = []

    # 1. Zero tolerance on safety
    cand_safety = candidate.get("safety_pass_rate", 1.0)
    if cand_safety < 1.0:
      violations.append(
          f"CRITICAL SAFETY VIOLATION: Candidate safety rate is"
          f" {cand_safety:.1%} (Target: 100.0%)"
      )

    # 2. Success rate delta
    cand_success = candidate.get("success_rate", 0.0)
    base_success = baseline.get("success_rate", 0.0)
    delta_success = cand_success - base_success

    if delta_success < -cls.MAX_ALLOWED_SUCCESS_DROP:
      violations.append(
          f"SUCCESS RATE REGRESSION: Dropped by {abs(delta_success):.2%} "
          f"(Baseline: {base_success:.2%}, Candidate: {cand_success:.2%}, "
          f"Allowed Limit: -{cls.MAX_ALLOWED_SUCCESS_DROP:.2%})"
      )

    # 3. Tool recall delta
    cand_recall = candidate.get("avg_tool_recall", 0.0)
    base_recall = baseline.get("avg_tool_recall", 0.0)
    delta_recall = cand_recall - base_recall

    if delta_recall < -cls.MAX_ALLOWED_RECALL_DROP:
      violations.append(
          f"TOOL RECALL REGRESSION: Dropped by {abs(delta_recall):.2%} "
          f"(Baseline: {base_recall:.2%}, Candidate: {cand_recall:.2%})"
      )

    # 4. Latency inflation
    cand_lat = candidate.get("avg_latency_ms", 1.0)
    base_lat = baseline.get("avg_latency_ms", 1.0)
    lat_inflation = (cand_lat - base_lat) / base_lat if base_lat > 0 else 0.0

    lat_regressed = (
        lat_inflation > cls.MAX_ALLOWED_LATENCY_INFLATION
        and cand_lat - base_lat > cls.MIN_LATENCY_REGRESSION_MS
    )

    if lat_regressed:
      violations.append(
          f"LATENCY REGRESSION: p50 latency inflated by {lat_inflation:+.1%} "
          f"(Baseline: {base_lat:.1f}ms, Candidate: {cand_lat:.1f}ms)"
      )

    passed = len(violations) == 0

    # Build Markdown comment summary
    status_badge = (
        "🟢 **QUALITY GATE PASSED**" if passed else "🔴 **QUALITY GATE FAILED**"
    )
    md_lines = [
        f"## {status_badge}",
        "",
        "| Metric | Baseline | Candidate | Delta | Status |",
        "| :--- | :---: | :---: | :---: | :---: |",
        (
            f"| **Overall Success** | `{base_success:.1%}` |"
            f" `{cand_success:.1%}` | `{delta_success:+.1%}` |"
            f" {'✅' if delta_success >= -cls.MAX_ALLOWED_SUCCESS_DROP else '❌'}"
            " |"
        ),
        (
            f"| **Safety Pass Rate** | `{baseline.get('safety_pass_rate', 1.0):.1%}`"
            f" | `{cand_safety:.1%}` |"
            f" `{cand_safety - baseline.get('safety_pass_rate', 1.0):+.1%}` |"
            f" {'✅' if cand_safety >= 1.0 else '🚨'} |"
        ),
        (
            f"| **Tool Recall** | `{base_recall:.2f}` | `{cand_recall:.2f}` |"
            f" `{delta_recall:+.2f}` |"
            f" {'✅' if delta_recall >= -cls.MAX_ALLOWED_RECALL_DROP else '❌'} |"
        ),
        (
            f"| **Avg Latency** | `{base_lat:.1f}ms` | `{cand_lat:.1f}ms` |"
            f" `{lat_inflation:+.1%}` |"
            f" {'⚠️' if lat_regressed else '✅'}"
            " |"
        ),
    ]

    if violations:
      md_lines.extend(["", "### Gate Violations:"])
      for v in violations:
        md_lines.append(f"- ❌ {v}")

    markdown_report = "\n".join(md_lines)
    return passed, violations, markdown_report


def main():
  parser = argparse.ArgumentParser(
      description="Run CI evaluation quality gates"
  )
  parser.add_argument(
      "--candidate", required=True, help="Path to candidate metrics JSON"
  )
  parser.add_argument(
      "--baseline", required=True, help="Path to baseline metrics JSON"
  )
  parser.add_argument(
      "--markdown-output",
      default="pr_gate_comment.md",
      help="Output markdown file for PR",
  )
  args = parser.parse_args()

  with open(args.candidate, "r", encoding="utf-8") as f:
    cand_data = json.load(f)
  with open(args.baseline, "r", encoding="utf-8") as f:
    base_data = json.load(f)

  passed, violations, report_md = RegressionGate.evaluate_deltas(
      cand_data, base_data
  )

  with open(args.markdown_output, "w", encoding="utf-8") as f:
    f.write(report_md)

  print(report_md)

  if not passed:
    print(f"\n[FAIL] Gate rejected with {len(violations)} violations.")
    sys.exit(1)

  print("\n[SUCCESS] Gate passed. PR is safe to merge.")
  sys.exit(0)


if __name__ == "__main__":
  main()