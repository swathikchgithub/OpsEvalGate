"""tests/test_baseline_gate.py - Stored baseline, loader, and latency noise floor."""

import json

import pytest

from evals.eval_gate import RegressionGate
from scripts import run_ci_gate

METRIC_KEYS = {
    "success_rate",
    "safety_pass_rate",
    "avg_tool_recall",
    "avg_latency_ms",
}


def _metrics(latency):
  return {
      "success_rate": 1.0,
      "safety_pass_rate": 1.0,
      "avg_tool_recall": 1.0,
      "avg_latency_ms": latency,
  }


def test_tiny_absolute_latency_increase_is_treated_as_noise():
  passed, _, _ = RegressionGate.evaluate_deltas(_metrics(0.5), _metrics(0.06))

  assert passed is True


def test_large_latency_increase_over_tiny_baseline_is_flagged():
  passed, _, _ = RegressionGate.evaluate_deltas(_metrics(10.0), _metrics(0.06))

  assert passed is False


def test_proportional_latency_regression_over_realistic_baseline_is_flagged():
  passed, violations, _ = RegressionGate.evaluate_deltas(
      _metrics(80.0), _metrics(50.0)
  )

  assert any("LATENCY REGRESSION" in v for v in violations)


def test_load_baseline_raises_with_regeneration_hint_when_missing(tmp_path):
  with pytest.raises(FileNotFoundError, match="--update-baseline"):
    run_ci_gate.load_baseline(tmp_path / "missing.json")


def test_write_then_load_baseline_round_trips_metrics(tmp_path):
  path = tmp_path / "baseline.json"
  run_ci_gate.write_baseline(_metrics(0.06), path)

  loaded = run_ci_gate.load_baseline(path)

  assert loaded["avg_latency_ms"] == 0.06


def test_written_baseline_records_how_it_was_generated(tmp_path):
  path = tmp_path / "baseline.json"
  run_ci_gate.write_baseline(_metrics(0.06), path)

  meta = json.loads(path.read_text())["meta"]

  assert meta["seed"] == run_ci_gate.SEED


def test_committed_baseline_has_all_gate_metrics():
  baseline = run_ci_gate.load_baseline()

  assert METRIC_KEYS <= set(baseline)


def test_committed_baseline_reflects_a_working_agent_not_a_zero_floor():
  baseline = run_ci_gate.load_baseline()

  assert baseline["success_rate"] > 0.0


def test_fresh_candidate_passes_against_committed_baseline():
  candidate = run_ci_gate.build_candidate_metrics()

  passed, violations, _ = RegressionGate.evaluate_deltas(
      candidate, run_ci_gate.load_baseline()
  )

  assert passed, violations
