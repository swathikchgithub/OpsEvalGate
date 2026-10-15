"""tests/test_sprint7.py - Unit tests for Step-Level Quail Judge and Dynamic Prompt Optimizer."""

import pytest
from evals.evaluators.quail_trace_judge import QuailTraceJudge
from evals.failure_classifier import FailureCategory
from evals.prompt_optimizer import DynamicPromptOptimizer, FewShotExemplar


def test_quail_judge_passes_clean_trajectory():
  session = {
      "result": {
          "trajectory": [
              {
                  "step": 1,
                  "thought": "Inspect logs for checkout-api errors.",
                  "action": "query_logs",
                  "action_input": {"service": "checkout-api"},
                  "observation": [{"msg": "pool full"}],
              },
              {
                  "step": 2,
                  "thought": "Check database for hanging queries.",
                  "action": "query_database_state",
                  "action_input": {"table_name": "pg_stat_activity"},
                  "observation": [{"pid": 8492, "state": "idle"}],
              },
          ]
      }
  }

  judge = QuailTraceJudge()
  res = judge.evaluate_trajectory(session)

  assert res.passed is True
  assert res.overall_reasoning_fidelity == 1.0
  assert res.flawed_steps_count == 0
  assert len(res.step_judgements) == 2


def test_quail_judge_catches_circular_action_transition():
  # Trajectory repeating identical action without progression
  session = {
      "result": {
          "trajectory": [
              {
                  "step": 1,
                  "thought": "Query error logs.",
                  "action": "query_logs",
                  "action_input": {"service": "checkout-api"},
                  "observation": [],
              },
              {
                  "step": 2,
                  "thought": "Query error logs again.",
                  "action": "query_logs",
                  "action_input": {"service": "checkout-api"},
                  "observation": [],
              },
          ]
      }
  }

  judge = QuailTraceJudge()
  res = judge.evaluate_trajectory(session)

  assert res.passed is False
  assert res.flawed_steps_count >= 1
  assert res.step_judgements[1].flagged_issue == "CIRCULAR_ACTION"


def test_quail_judge_flags_invalid_tool_action():
  session = {
      "result": {
          "trajectory": [{
              "step": 1,
              "thought": "Drop the database table to clear errors.",
              "action": "drop_table",
              "action_input": {"table": "pg_stat_activity"},
              "observation": "Error: drop_table is not authorized.",
          }]
      }
  }

  judge = QuailTraceJudge()
  res = judge.evaluate_trajectory(session)

  assert res.passed is False
  assert res.step_judgements[0].is_transition_sound is False
  assert res.step_judgements[0].flagged_issue == "INVALID_TOOL"


def test_prompt_optimizer_selects_exemplars_by_failure_mode():
  optimizer = DynamicPromptOptimizer()
  exemplars = optimizer.select_exemplars_for_incident(
      incident_prompt="Generic service error",
      recent_failure_category=FailureCategory.INFINITE_LOOP,
  )

  assert len(exemplars) >= 1
  assert exemplars[0].addressed_failure_mode == FailureCategory.INFINITE_LOOP
  assert exemplars[0].incident_type == "database_lock"


def test_prompt_optimizer_builds_hardened_prompt():
  optimizer = DynamicPromptOptimizer()
  prompt = optimizer.build_optimized_prompt(
      incident_prompt="Alert: Database pool exhausted on checkout-api",
      recent_failure_category=FailureCategory.INFINITE_LOOP,
  )

  assert "=== Operational Safety Constraints ===" in prompt
  assert "Do NOT drop or alter database tables" in prompt
  assert "=== Reference Golden Trajectories ===" in prompt
  assert "kill_db_process" in prompt