"""tests/test_agent_router.py - Test suite for model routing and in-flight escalation."""

import pytest
from agent_router import IncidentModelRouter, ModelTier


def test_router_routes_simple_log_query_to_lightweight():
  router = IncidentModelRouter()
  decision = router.route_incident("fetch logs for auth-service")

  assert decision.selected_tier == ModelTier.LIGHTWEIGHT
  assert decision.complexity_score < router.complexity_threshold
  assert "Direct log fetch" in decision.detected_indicators


def test_router_routes_deadlock_and_sev1_to_frontier():
  router = IncidentModelRouter()
  decision = router.route_incident(
      "SEV1 Alert: checkout-api database connection pool exhausted due to idle in transaction lock",
      severity="SEV1",
  )

  assert decision.selected_tier == ModelTier.FRONTIER_REASONING
  assert decision.complexity_score >= router.complexity_threshold
  assert any("lock contention" in ind.lower() for ind in decision.detected_indicators)


def test_router_in_flight_escalation_on_error():
  router = IncidentModelRouter()

  # Should escalate immediately if an error occurred during execution
  should_escalate = router.should_escalate_in_flight(
      current_step=1,
      trajectory=[{"step": 1, "action": "query_logs"}],
      has_error=True,
  )
  assert should_escalate is True


def test_router_in_flight_escalation_on_stall_or_loop():
  router = IncidentModelRouter()

  # Consecutive repeated action at step 2 indicates reasoning stall
  trajectory = [
      {"step": 1, "action": "query_logs"},
      {"step": 2, "action": "query_logs"},
  ]
  should_escalate = router.should_escalate_in_flight(
      current_step=2,
      trajectory=trajectory,
      has_error=False,
  )
  assert should_escalate is True


def test_router_in_flight_no_escalation_on_smooth_progress():
  router = IncidentModelRouter()

  # Healthy progression from log search to diagnostic table check
  trajectory = [
      {"step": 1, "action": "query_logs"},
      {"step": 2, "action": "query_database_state"},
  ]
  should_escalate = router.should_escalate_in_flight(
      current_step=2,
      trajectory=trajectory,
      has_error=False,
  )
  assert should_escalate is False