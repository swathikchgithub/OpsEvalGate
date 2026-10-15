"""evals/mock_infra.py - Isolated In-Memory Infrastructure Environment Factory."""

from __future__ import annotations
from contextlib import contextmanager
import copy
from typing import Any, Callable, Dict, List, Optional
from evals.schemas import IncidentScenario, MockInfraSeed
from tracer import trace_span


class MockEnvironment:

  def __init__(self, seed: Optional[MockInfraSeed] = None):
    self.logs: List[Dict[str, Any]] = []
    self.db_tables: Dict[str, List[Dict[str, Any]]] = {}
    self.metric_series: Dict[str, List[float]] = {}
    self.mutation_history: List[Dict[str, Any]] = []
    if seed:
      self.hydrate(seed)

  def hydrate(self, seed: MockInfraSeed) -> None:
    self.logs = copy.deepcopy(seed.logs)
    self.db_tables = copy.deepcopy(seed.db_tables)
    self.metric_series = copy.deepcopy(seed.metric_series)
    self.mutation_history.clear()

  def reset(self) -> None:
    self.logs.clear()
    self.db_tables.clear()
    self.metric_series.clear()
    self.mutation_history.clear()

  def query_logs(
      self, service: str, level: Optional[str] = "ERROR", limit: int = 10
  ) -> List[Dict[str, Any]]:
    return [
        e
        for e in self.logs
        if e.get("service") == service
        and (level is None or e.get("level", "").upper() == level.upper())
    ][:limit]

  def query_database_state(
      self, table_name: str, filter_col: Optional[str] = None, filter_val: Any = None
  ) -> List[Dict[str, Any]]:
    rows = self.db_tables.get(table_name, [])
    if filter_col is not None:
      return [r for r in rows if r.get(filter_col) == filter_val]
    return copy.deepcopy(rows)

  def kill_db_process(self, pid: int) -> Dict[str, str]:
    table = self.db_tables.get("pg_stat_activity", [])
    for row in table:
      if row.get("pid") == pid:
        row["state"] = "terminated"
        self.mutation_history.append({"action": "kill_db_process", "pid": pid})
        return {"status": "SUCCESS", "message": f"PID {pid} terminated."}
    return {"status": "NOT_FOUND", "message": f"PID {pid} not found."}

  def get_bound_tools(self) -> Dict[str, Callable]:

    @trace_span(name="tool.query_logs", span_kind="TOOL")
    def _query_logs(
        service: str, level: Optional[str] = "ERROR", limit: int = 10
    ):
      return self.query_logs(service=service, level=level, limit=limit)

    @trace_span(name="tool.query_database_state", span_kind="TOOL")
    def _query_db(
        table_name: str,
        filter_col: Optional[str] = None,
        filter_val: Any = None,
    ):
      return self.query_database_state(
          table_name=table_name, filter_col=filter_col, filter_val=filter_val
      )

    @trace_span(name="tool.kill_db_process", span_kind="TOOL")
    def _kill_db(pid: int):
      return self.kill_db_process(pid=pid)

    return {
        "query_logs": _query_logs,
        "query_database_state": _query_db,
        "kill_db_process": _kill_db,
    }


class MockEnvironmentFactory:

  @staticmethod
  def create_from_scenario(scenario: IncidentScenario) -> MockEnvironment:
    return MockEnvironment(seed=scenario.infra_seed)

  @staticmethod
  @contextmanager
  def sandbox(scenario: IncidentScenario):
    env = MockEnvironmentFactory.create_from_scenario(scenario)
    try:
      yield env
    finally:
      env.reset()
