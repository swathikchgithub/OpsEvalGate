"""evals/offline_planner.py - Deterministic playbook agent for offline CI baselines.

Plans from the incident report and tool observations only, the same inputs a
live model would see. It never reads a scenario's answer keys, so scores
reflect pipeline and rule behavior and can fall below 1.0.
"""

from __future__ import annotations

import difflib
import json
from typing import Any, Dict, List, Optional, Sequence

from tracer import trace_span

FUZZY_CUTOFF = 0.8
MAX_REPORT_CHARS = 240
IDLE_IN_TXN = "idle in transaction"

# Runbook: log symptom keywords -> database table to inspect next.
SYMPTOM_TABLES = (
    (("pool", "connection", "lock", "transaction"), "pg_stat_activity"),
    (("token", "worker", "starvation"), "token_blacklist"),
)

Plan = Dict[str, Any]
Steps = List[Dict[str, Any]]


class PlaybookPlanner:
  """Rule-based ReAct planner usable as ReActAgent's `model_client`."""

  def __init__(self, services: Sequence[str]):
    self.services = tuple(services)

  @trace_span(name="llm.reason_and_plan", span_kind="LLM")
  def __call__(
      self,
      prompt: str,
      iteration: int,
      scratchpad: Optional[Steps] = None,
      **kwargs: Any,
  ) -> Plan:
    steps = scratchpad or []
    if not steps:
      return self._first_step(prompt)
    handlers = {
        "query_logs": self._after_logs,
        "query_database_state": self._after_db,
    }
    handler = handlers.get(steps[-1].get("action"), self._finish)
    return handler(prompt, steps)

  def _find_service(self, prompt: str) -> Optional[str]:
    by_lower = {s.lower(): s for s in self.services}
    text = prompt.lower()
    for lowered, original in by_lower.items():
      if lowered in text:
        return original
    for token in text.split():
      close = difflib.get_close_matches(
          token.strip(".,:;!?|()[]'\""), list(by_lower), n=1, cutoff=FUZZY_CUTOFF
      )
      if close:
        return by_lower[close[0]]
    return None

  def _first_step(self, prompt: str) -> Plan:
    service = self._find_service(prompt)
    if service is None:
      return {
          "thought": "No known service named in the report.",
          "final_answer": (
              "Unable to identify the affected service from the report;"
              " escalating to a human responder."
          ),
      }
    return {
        "thought": f"Inspect ERROR logs for {service}.",
        "action": "query_logs",
        "action_input": {"service": service, "level": "ERROR"},
    }

  def _after_logs(self, prompt: str, steps: Steps) -> Plan:
    text = " ".join(m.lower() for m in _log_messages(steps[-1]))
    for keywords, table in SYMPTOM_TABLES:
      if any(k in text for k in keywords):
        return {
            "thought": f"Log symptoms point at {table}; inspecting it.",
            "action": "query_database_state",
            "action_input": {"table_name": table},
        }
    return self._finish(prompt, steps)

  def _after_db(self, prompt: str, steps: Steps) -> Plan:
    blockers = [
        r for r in _rows(steps[-1])
        if r.get("state") == IDLE_IN_TXN and isinstance(r.get("pid"), int)
    ]
    if not blockers:
      return self._finish(prompt, steps)
    pid = max(blockers, key=lambda r: r.get("duration_sec", 0))["pid"]
    return {
        "thought": f"PID {pid} is {IDLE_IN_TXN}; terminating it.",
        "action": "kill_db_process",
        "action_input": {"pid": pid},
    }

  def _finish(self, prompt: str, steps: Steps) -> Plan:
    evidence = [e for e in (_describe(s) for s in steps) if e]
    evidence.append(f"Reported symptom: {prompt.strip()[:MAX_REPORT_CHARS]}")
    return {
        "thought": "Evidence gathered; reporting resolution.",
        "final_answer": " ".join(evidence),
    }


def _rows(step: Dict[str, Any]) -> List[Dict[str, Any]]:
  obs = step.get("observation")
  return [r for r in obs if isinstance(r, dict)] if isinstance(obs, list) else []


def _log_messages(step: Dict[str, Any]) -> List[str]:
  return [str(r.get("msg", "")) for r in _rows(step)]


def _describe(step: Dict[str, Any]) -> str:
  action = step.get("action")
  if action == "query_logs":
    return "Log evidence: " + "; ".join(_log_messages(step)[:2]) + "."
  if action == "kill_db_process":
    obs = step.get("observation")
    return f"Mitigation: {obs.get('message', obs)}" if isinstance(obs, dict) else ""
  if action == "query_database_state":
    return _describe_db(step)
  return ""


def _describe_db(step: Dict[str, Any]) -> str:
  rows = _rows(step)
  idle = [r for r in rows if r.get("state") == IDLE_IN_TXN]
  if idle:
    return " ".join(
        f"Session pid {r.get('pid')} is {IDLE_IN_TXN} and holding a lock."
        for r in idle
    )
  table = (step.get("action_input") or {}).get("table_name", "table")
  return f"Database state {table}: {json.dumps(rows, default=str)}."
