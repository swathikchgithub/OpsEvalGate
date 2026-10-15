"""tests/test_offline_planner.py - Playbook planner: deterministic offline agent for CI baselines."""

import inspect

import pytest

from agent_core import ReActAgent
from evals.evaluators.deterministic import DeterministicGuards
from evals.evaluators.trajectory_metrics import TrajectoryMetricsEvaluator
from evals.mock_infra import MockEnvironmentFactory
from evals.runner import EvaluationRunner
from evals.scenario_generator import SyntheticScenarioGenerator
from evals import offline_planner
from evals.offline_planner import PlaybookPlanner
from evals.schemas import PersonaArchetype

CHECKOUT = 0
AUTH = 1
ALLOWED_TOOLS = {"query_logs", "query_database_state", "kill_db_process"}


def _scenario(incident_index):
  return SyntheticScenarioGenerator(seed=1).generate_scenario(
      archetype=PersonaArchetype.TERSE_SRE,
      incident_index=incident_index,
      apply_noise=False,
  )


def _run(scenario, prompt=None):
  with MockEnvironmentFactory.sandbox(scenario) as env:
    services = sorted({e["service"] for e in env.logs})
    agent = ReActAgent(
        tools=env.get_bound_tools(), model_client=PlaybookPlanner(services)
    )
    return agent.run(prompt or scenario.raw_user_prompt), env


def _actions(result):
  return [s["action"] for s in result["trajectory"]]


def test_checkout_incident_follows_logs_then_db_then_kill():
  result, _ = _run(_scenario(CHECKOUT))

  assert _actions(result) == [
      "query_logs",
      "query_database_state",
      "kill_db_process",
  ]


def test_auth_incident_inspects_token_table_and_never_kills():
  result, _ = _run(_scenario(AUTH))

  assert _actions(result) == ["query_logs", "query_database_state"]


def test_auth_incident_probes_token_blacklist_table():
  result, _ = _run(_scenario(AUTH))

  assert result["trajectory"][1]["action_input"] == {
      "table_name": "token_blacklist"
  }


def test_kill_target_pid_comes_from_observation_not_constants():
  scenario = _scenario(CHECKOUT)
  scenario.infra_seed.db_tables["pg_stat_activity"][0]["pid"] = 1234

  result, _ = _run(scenario)

  assert result["trajectory"][2]["action_input"] == {"pid": 1234}


def test_no_kill_when_no_session_is_idle_in_transaction():
  scenario = _scenario(CHECKOUT)
  scenario.infra_seed.db_tables["pg_stat_activity"][0]["state"] = "active"

  result, _ = _run(scenario)

  assert "kill_db_process" not in _actions(result)


def test_run_completes_with_resolution_for_both_incidents():
  statuses = [_run(_scenario(i))[0]["status"] for i in (CHECKOUT, AUTH)]

  assert statuses == ["COMPLETED", "COMPLETED"]


@pytest.mark.parametrize("incident_index", [CHECKOUT, AUTH])
def test_deterministic_evaluators_pass_on_clean_prompt(incident_index):
  scenario = _scenario(incident_index)
  result, _ = _run(scenario)

  verdict = DeterministicGuards().evaluate({"result": result}, scenario)

  assert verdict.passed, [v.details for v in verdict.violations]


@pytest.mark.parametrize("incident_index", [CHECKOUT, AUTH])
def test_trajectory_evaluator_passes_on_clean_prompt(incident_index):
  scenario = _scenario(incident_index)
  result, _ = _run(scenario)

  verdict = TrajectoryMetricsEvaluator().evaluate(
      {"result": result}, scenario
  )

  assert verdict.passed, verdict.summary


def test_typo_corrupted_service_name_is_still_resolved():
  scenario = _scenario(CHECKOUT)

  result, _ = _run(scenario, prompt="HELP!! checkout-4pi is throwing 500s")

  assert result["trajectory"][0]["action_input"]["service"] == "checkout-api"


def test_unknown_service_ends_immediately_without_calling_tools():
  result, _ = _run(_scenario(CHECKOUT), prompt="the printer on floor 3 is jammed")

  assert result["trajectory"] == []


def test_unknown_service_still_returns_a_completed_resolution():
  result, _ = _run(_scenario(CHECKOUT), prompt="the printer on floor 3 is jammed")

  assert result["status"] == "COMPLETED"


@pytest.mark.parametrize("incident_index", [CHECKOUT, AUTH])
def test_planner_only_uses_registered_non_forbidden_tools(incident_index):
  scenario = _scenario(incident_index)
  result, _ = _run(scenario)

  used = set(_actions(result))

  assert used <= ALLOWED_TOOLS - set(scenario.forbidden_actions)


def test_planner_is_deterministic_for_identical_inputs():
  first, _ = _run(_scenario(CHECKOUT))
  second, _ = _run(_scenario(CHECKOUT))

  assert first["resolution"] == second["resolution"]


@pytest.mark.parametrize(
    "forbidden_marker",
    ["golden_trajectory", "expected_resolution_keywords", "scenario_generator"],
)
def test_planner_source_never_reads_scenario_answer_keys(forbidden_marker):
  source = inspect.getsource(offline_planner)

  assert forbidden_marker not in source


def test_runner_reports_nonzero_success_rate_with_playbook_planner():
  batch = SyntheticScenarioGenerator(seed=42).generate_batch(count=4)

  summary = EvaluationRunner(trace_output_dir=None).run_batch(batch)

  assert summary.success_rate > 0.0
