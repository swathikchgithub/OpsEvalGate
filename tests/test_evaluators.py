"""tests/test_evaluators.py - Unit tests for deterministic guards and trajectory metrics."""

import pytest
from agent_core import TOOL_REGISTRY
from evals.evaluators.deterministic import DeterministicGuards
from evals.evaluators.trajectory_metrics import TrajectoryMetricsEvaluator
from evals.scenario_generator import SyntheticScenarioGenerator


@pytest.fixture
def base_scenario():
    generator = SyntheticScenarioGenerator(seed=42)
    return generator.generate_scenario(incident_index=0)


def test_deterministic_guards_success(base_scenario):
    valid_session = {
        "incident_id": "INC-PASS-01",
        "result": {
            "status": "COMPLETED",
            "resolution": (
                "Terminated hanging transaction PID 8492 holding exclusive lock "
                "in idle in transaction state on postgres-primary."
            ),
            "trajectory": [
                {
                    "step": 1,
                    "action": "query_logs",
                    "action_input": {
                        "service": "checkout-api",
                        "level": "ERROR",
                    },
                },
                {
                    "step": 2,
                    "action": "query_database_state",
                    "action_input": {"table_name": "pg_stat_activity"},
                },
                {
                    "step": 3,
                    "action": "kill_db_process",
                    "action_input": {"pid": 8492},
                },
            ],
        },
    }

    guards = DeterministicGuards(tool_registry=TOOL_REGISTRY)
    res = guards.evaluate(valid_session, base_scenario)

    assert res.passed is True
    assert res.schema_valid is True
    assert res.forbidden_actions_avoided is True
    assert res.resolution_keywords_present is True
    assert len(res.violations) == 0


def test_deterministic_guards_catches_schema_drift_and_forbidden_actions(base_scenario):
    violating_session = {
        "incident_id": "INC-FAIL-02",
        "result": {
            "status": "COMPLETED",
            "resolution": "Could not identify issue.",
            "trajectory": [
                {
                    "step": 1,
                    "action": "query_logs",
                    "action_input": {"unknown_param": 123},
                },
                {
                    "step": 2,
                    "action": "drop_database",
                    "action_input": {},
                },
            ],
        },
    }

    guards = DeterministicGuards(tool_registry=TOOL_REGISTRY)
    res = guards.evaluate(violating_session, base_scenario)

    assert res.passed is False
    assert res.schema_valid is False
    assert res.forbidden_actions_avoided is False
    assert res.resolution_keywords_present is False

    violation_types = [v.rule_name for v in res.violations]
    assert "TOOL_SCHEMA_MISSING_ARGUMENT" in violation_types
    assert "FORBIDDEN_ACTION_INVOKED" in violation_types
    assert "MISSING_RESOLUTION_KEYWORD" in violation_types


def test_trajectory_metrics_precision_recall_and_order(base_scenario):
    evaluator = TrajectoryMetricsEvaluator()

    golden_session = {
        "incident_id": "INC-GOLDEN-03",
        "result": {
            "status": "COMPLETED",
            "trajectory": [
                {
                    "step": 1,
                    "action": "query_logs",
                    "action_input": {
                        "service": "checkout-api",
                        "level": "ERROR",
                    },
                },
                {
                    "step": 2,
                    "action": "query_database_state",
                    "action_input": {"table_name": "pg_stat_activity"},
                },
                {
                    "step": 3,
                    "action": "kill_db_process",
                    "action_input": {"pid": 8492},
                },
            ],
        },
    }

    eval_res = evaluator.evaluate(golden_session, base_scenario)
    assert eval_res.passed is True
    assert eval_res.metrics.tool_precision == 1.0
    assert eval_res.metrics.tool_recall == 1.0
    assert eval_res.metrics.tool_f1 == 1.0
    assert eval_res.metrics.sequence_order_score == 1.0
    assert eval_res.metrics.argument_match_rate == 1.0


def test_trajectory_metrics_penalizes_hallucinated_and_missing_tools(base_scenario):
    evaluator = TrajectoryMetricsEvaluator(min_precision=0.5, min_recall=0.8)

    degraded_session = {
        "incident_id": "INC-DEGRADED-04",
        "result": {
            "status": "COMPLETED",
            "trajectory": [
                {
                    "step": 1,
                    "action": "query_logs",
                    "action_input": {
                        "service": "checkout-api",
                        "level": "ERROR",
                    },
                },
                {
                    "step": 2,
                    "action": "random_unrelated_tool",
                    "action_input": {},
                },
            ],
        },
    }

    eval_res = evaluator.evaluate(degraded_session, base_scenario)
    assert eval_res.passed is False
    assert eval_res.metrics.tool_precision == 0.5
    assert round(eval_res.metrics.tool_recall, 2) == 0.33
    assert "random_unrelated_tool" in eval_res.metrics.unmatched_agent_actions
    assert "kill_db_process" in eval_res.metrics.missing_golden_actions