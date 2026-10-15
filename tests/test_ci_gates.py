"""tests/test_ci_gates.py - Tests for CI evaluation runner and regression gates."""

import pytest
from evals.eval_gate import RegressionGate
from evals.runner import EvaluationRunner
from evals.scenario_generator import SyntheticScenarioGenerator


def test_regression_gate_passes_healthy_candidate():
  baseline = {
      "success_rate": 0.85,
      "safety_pass_rate": 1.0,
      "avg_tool_recall": 0.90,
      "avg_latency_ms": 50.0,
  }
  candidate = {
      "success_rate": 0.88,  # +3%
      "safety_pass_rate": 1.0,  # 100%
      "avg_tool_recall": 0.92,  # +2%
      "avg_latency_ms": 52.0,  # +4%
  }

  passed, violations, md = RegressionGate.evaluate_deltas(candidate, baseline)
  assert passed is True
  assert len(violations) == 0
  assert "QUALITY GATE PASSED" in md


def test_regression_gate_rejects_safety_violation():
  baseline = {
      "success_rate": 0.85,
      "safety_pass_rate": 1.0,
      "avg_tool_recall": 0.90,
      "avg_latency_ms": 50.0,
  }
  candidate = {
      "success_rate": 0.88,
      "safety_pass_rate": 0.95,  # Dropped safety below 1.0
      "avg_tool_recall": 0.90,
      "avg_latency_ms": 50.0,
  }

  passed, violations, _ = RegressionGate.evaluate_deltas(candidate, baseline)
  assert passed is False
  assert any("CRITICAL SAFETY VIOLATION" in v for v in violations)


def test_regression_gate_rejects_accuracy_drop():
  baseline = {
      "success_rate": 0.90,
      "safety_pass_rate": 1.0,
      "avg_tool_recall": 0.90,
      "avg_latency_ms": 50.0,
  }
  candidate = {
      "success_rate": 0.80,  # -10% drop (limit is -2%)
      "safety_pass_rate": 1.0,
      "avg_tool_recall": 0.90,
      "avg_latency_ms": 50.0,
  }

  passed, violations, _ = RegressionGate.evaluate_deltas(candidate, baseline)
  assert passed is False
  assert any("SUCCESS RATE REGRESSION" in v for v in violations)


def test_evaluation_runner_batch_run():
  gen = SyntheticScenarioGenerator(seed=99)
  batch = gen.generate_batch(count=2)
  runner = EvaluationRunner(trace_output_dir=None)

  summary = runner.run_batch(batch)
  assert summary.total_scenarios == 2
  assert summary.safety_pass_rate >= 0.0
  assert len(summary.run_reports) == 2
  assert summary.run_reports[0].target_service in [
      "checkout-api",
      "auth-service",
      "payment-gateway",
  ]