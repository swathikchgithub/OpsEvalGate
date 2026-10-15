"""agent_core.py - Core ReAct (Reasoning + Action) execution loop and SRE diagnostic tools."""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, List, Optional
from tracer import trace_span

# ---------------------------------------------------------------------------
# In-Memory SRE Infrastructure Mock Sandbox
# ---------------------------------------------------------------------------

MOCK_LOGS = [
    {
        "timestamp": "2026-10-12T00:01:00Z",
        "service": "checkout-api",
        "level": "INFO",
        "msg": "Order batch processing started.",
    },
    {
        "timestamp": "2026-10-12T00:03:15Z",
        "service": "checkout-api",
        "level": "ERROR",
        "msg": (
            "FATAL: Connection pool exhausted. DB connection timeout after"
            " 5000ms."
        ),
    },
    {
        "timestamp": "2026-10-12T00:03:18Z",
        "service": "checkout-api",
        "level": "ERROR",
        "msg": (
            "HTTP 500 returned to client. Upstream postgres-primary"
            " unreachable."
        ),
    },
    {
        "timestamp": "2026-10-12T00:04:00Z",
        "service": "auth-service",
        "level": "INFO",
        "msg": "Token verification rate normal: 450 req/sec.",
    },
]

MOCK_DATABASE = {
    "pg_stat_activity": [
        {
            "pid": 8492,
            "user": "checkout_worker",
            "state": "idle in transaction",
            "query": (
                "UPDATE orders SET status = 'PROCESSING' WHERE batch_id ="
                " 'B-9982'"
            ),
            "duration_sec": 412,
        },
        {
            "pid": 9011,
            "user": "readonly_replica",
            "state": "active",
            "query": "SELECT * FROM health_check LIMIT 1",
            "duration_sec": 0,
        },
    ]
}


# ---------------------------------------------------------------------------
# SRE Diagnostic Tool Registry
# ---------------------------------------------------------------------------


@trace_span(name="tool.query_logs", span_kind="TOOL")
def query_logs(
    service: str, level: str = "ERROR", limit: int = 5
) -> List[Dict[str, Any]]:
  """Query service log entries.

  Arguments: service (str, e.g. 'checkout-api'), level (str, e.g. 'ERROR'),
  limit (int).
  """
  matches = [
      entry
      for entry in MOCK_LOGS
      if entry["service"] == service and entry["level"] == level.upper()
  ]
  return matches[:limit]


@trace_span(name="tool.query_database_state", span_kind="TOOL")
def query_database_state(table_name: str) -> List[Dict[str, Any]]:
  """Inspect Postgres database state table.

  Arguments: table_name (str, e.g. 'pg_stat_activity').
  """
  return MOCK_DATABASE.get(table_name, [])


@trace_span(name="tool.kill_db_process", span_kind="TOOL")
def kill_db_process(pid: int) -> Dict[str, str]:
  """Terminate a blocking backend database connection.

  Arguments: pid (int, the process ID to terminate, e.g. 8492).
  """
  for row in MOCK_DATABASE.get("pg_stat_activity", []):
    if row["pid"] == int(pid):
      row["state"] = "terminated"
      return {"status": "SUCCESS", "message": f"Terminated connection {pid}"}
  return {"status": "NOT_FOUND", "message": f"PID {pid} does not exist"}


TOOL_REGISTRY: Dict[str, Callable] = {
    "query_logs": query_logs,
    "query_database_state": query_database_state,
    "kill_db_process": kill_db_process,
}


# ---------------------------------------------------------------------------
# Autonomous ReAct Agent Core
# ---------------------------------------------------------------------------


class ReActAgent:
  """Autonomous SRE Diagnostic Agent implementing the ReAct loop with OpenInference tracing."""

  def __init__(
      self,
      tools: Optional[Dict[str, Callable]] = None,
      model_client: Optional[Callable[..., Dict[str, Any]]] = None,
      max_iterations: int = 5,
  ):
    self.tools = tools or TOOL_REGISTRY
    self.model_client = model_client or self._mock_model_planner
    self.max_iterations = max_iterations

  @trace_span(name="llm.reason_and_plan", span_kind="LLM")
  def _mock_model_planner(
      self,
      prompt: str,
      iteration: int,
      scratchpad: Optional[List[Dict[str, Any]]] = None,
      **kwargs: Any,
  ) -> Dict[str, Any]:
    """Default deterministic planner for offline test suites."""
    if iteration == 1:
      return {
          "thought": (
              "Checkout API is reporting issues. Let's inspect ERROR logs for"
              " checkout-api."
          ),
          "action": "query_logs",
          "action_input": {"service": "checkout-api", "level": "ERROR"},
      }
    elif iteration == 2:
      return {
          "thought": (
              "Logs show connection pool exhaustion. Checking pg_stat_activity"
              " for blocking queries."
          ),
          "action": "query_database_state",
          "action_input": {"table_name": "pg_stat_activity"},
      }
    elif iteration == 3:
      return {
          "thought": (
              "Found PID 8492 stuck in 'idle in transaction' locking orders."
              " Terminating PID 8492."
          ),
          "action": "kill_db_process",
          "action_input": {"pid": 8492},
      }
    else:
      return {
          "thought": "Hanging process terminated. Incident mitigated.",
          "final_answer": (
              "Mitigation complete: Terminated hanging transaction PID 8492 on"
              " postgres-primary."
          ),
      }

  @trace_span(name="agent.react_loop", span_kind="AGENT")
  def run(self, incident_description: str) -> Dict[str, Any]:
    """Executes the autonomous ReAct triage loop."""
    trajectory: List[Dict[str, Any]] = []

    for i in range(1, self.max_iterations + 1):
      # 1. Ask model for next step, passing accumulated trajectory as scratchpad
      step_plan = self.model_client(
          prompt=incident_description,
          iteration=i,
          scratchpad=trajectory,
      )

      # 2. Check for completion
      final_text = None
      if "final_answer" in step_plan:
        final_text = step_plan["final_answer"]
      elif "resolution" in step_plan and not step_plan.get("action"):
        final_text = step_plan["resolution"]
      elif step_plan.get("action") in ("finish", "complete", "final_answer"):
        final_text = step_plan.get(
            "action_input", step_plan.get("thought", "Incident mitigated.")
        )
        if isinstance(final_text, dict):
          final_text = final_text.get("answer", str(final_text))

      if final_text:
        return {
            "status": "COMPLETED",
            "resolution": str(final_text),
            "steps_executed": i,
            "trajectory": trajectory,
        }

      # 3. Extract action & parameters
      action_name = step_plan.get("action")
      action_input = step_plan.get("action_input", {})
      thought = step_plan.get("thought", "")

      if not action_name:
        observation = (
            "Error: No action or final_answer was specified in plan JSON."
        )
      elif action_name not in self.tools:
        observation = (
            f"Error: Tool '{action_name}' is not in the registered tool"
            " directory."
        )
      else:
        # 4. Invoke tool
        tool_fn = self.tools[action_name]
        try:
          if isinstance(action_input, dict):
            observation = tool_fn(**action_input)
          else:
            observation = tool_fn(action_input)
        except Exception as e:
          observation = f"Tool execution failed: {type(e).__name__}: {e}"

      # 5. Append step record to trajectory
      trajectory.append({
          "step": i,
          "thought": thought,
          "action": action_name,
          "action_input": action_input,
          "observation": observation,
      })

    return {
        "status": "MAX_ITERATIONS_REACHED",
        "resolution": "Could not mitigate within iteration limit.",
        "steps_executed": self.max_iterations,
        "trajectory": trajectory,
    }