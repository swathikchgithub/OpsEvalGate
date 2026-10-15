#!/usr/bin/env python3
"""make_zip.py - Assembles the OpsCopilot project and creates opscopilot.zip."""

import os
import zipfile

FILES = {
    # ---------------------------------------------------------
    # Configuration & Requirements
    # ---------------------------------------------------------
    "pytest.ini": """[pytest]
pythonpath = .
testpaths = tests
""",
    "requirements.txt": """pydantic>=2.5.0
pytest>=8.0.0
""",
    # ---------------------------------------------------------
    # Sprint 1: Tracing, Agent Core, Replay
    # ---------------------------------------------------------
    "tracer.py": '''"""tracer.py - OpenInference-compatible tracing engine for OpsCopilot."""

from __future__ import annotations

import contextvars
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import functools
import json
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

_CURRENT_SPAN_ID = contextvars.ContextVar("current_span_id", default=None)


@dataclass
class Span:
  span_id: str
  name: str
  span_kind: str  # "CHAIN", "AGENT", "LLM", or "TOOL"
  parent_id: Optional[str]
  start_time: str
  end_time: Optional[str] = None
  latency_ms: Optional[float] = None
  status: str = "UNSET"  # "OK", "ERROR"
  status_message: Optional[str] = None
  attributes: Dict[str, Any] = field(default_factory=dict)
  inputs: Optional[Any] = None
  outputs: Optional[Any] = None

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


class InMemorySpanCollector:

  def __init__(self):
    self.spans: List[Span] = []

  def record_span(self, span: Span) -> None:
    self.spans.append(span)

  def clear(self) -> None:
    self.spans.clear()

  def export_json(self, indent: int = 2) -> str:
    return json.dumps([s.to_dict() for s in self.spans], indent=indent)

  def get_spans_by_kind(self, kind: str) -> List[Span]:
    return [s for s in self.spans if s.span_kind == kind]


GLOBAL_TRACER = InMemorySpanCollector()


def trace_span(
    name: Optional[str] = None,
    span_kind: str = "CHAIN",
    attributes: Optional[Dict[str, Any]] = None,
):
  """Decorator to instrument synchronous methods and log execution spans."""

  def decorator(func: Callable):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
      span_name = name or func.__name__
      span_id = str(uuid.uuid4())
      parent_id = _CURRENT_SPAN_ID.get()

      start_iso = datetime.now(timezone.utc).isoformat()
      start_perf = time.perf_counter()

      call_inputs = kwargs.copy()
      if args and len(args) > 1:
        call_inputs["_args"] = [str(a) for a in args[1:]]
      elif args and len(args) == 1 and not hasattr(args[0], "__dict__"):
        call_inputs["_args"] = str(args[0])

      span = Span(
          span_id=span_id,
          name=span_name,
          span_kind=span_kind,
          parent_id=parent_id,
          start_time=start_iso,
          attributes=dict(attributes or {}),
          inputs=call_inputs,
      )

      token = _CURRENT_SPAN_ID.set(span_id)
      try:
        result = func(*args, **kwargs)
        span.outputs = result
        span.status = "OK"
        return result
      except Exception as exc:
        span.status = "ERROR"
        span.status_message = str(exc)
        raise
      finally:
        latency = (time.perf_counter() - start_perf) * 1000
        span.latency_ms = round(latency, 2)
        span.end_time = datetime.now(timezone.utc).isoformat()
        GLOBAL_TRACER.record_span(span)
        _CURRENT_SPAN_ID.reset(token)

    return wrapper

  return decorator
''',
    "agent_core.py": '''"""agent_core.py - OpsCopilot ReAct Engine and Incident Diagnostics Tools."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from tracer import GLOBAL_TRACER, trace_span

MOCK_LOGS = [
    {
        "timestamp": "2026-10-11T23:14:02Z",
        "service": "checkout-api",
        "level": "ERROR",
        "msg": "Connection pool exhausted: max 50 reached",
    },
    {
        "timestamp": "2026-10-11T23:14:05Z",
        "service": "postgres-primary",
        "level": "WARN",
        "msg": "Idle transaction timeout triggered for client pid=8492",
    },
    {
        "timestamp": "2026-10-11T23:15:10Z",
        "service": "checkout-api",
        "level": "FATAL",
        "msg": "HTTP 500 returned to 48 upstream gateway clients",
    },
]

MOCK_DATABASE = {
    "pg_stat_activity": [
        {
            "pid": 8492,
            "state": "idle in transaction",
            "query": "SELECT * FROM orders FOR UPDATE",
            "duration_sec": 742,
        },
        {
            "pid": 8493,
            "state": "active",
            "query": "SELECT 1",
            "duration_sec": 0,
        },
    ]
}


@trace_span(name="tool.query_logs", span_kind="TOOL")
def query_logs(
    service: str, level: str = "ERROR", limit: int = 5
) -> List[Dict[str, Any]]:
  matches = [
      entry
      for entry in MOCK_LOGS
      if entry["service"] == service
      and entry["level"] == level.upper()
  ]
  return matches[:limit]


@trace_span(name="tool.query_database_state", span_kind="TOOL")
def query_database_state(table_name: str) -> List[Dict[str, Any]]:
  return MOCK_DATABASE.get(table_name, [])


@trace_span(name="tool.kill_db_process", span_kind="TOOL")
def kill_db_process(pid: int) -> Dict[str, str]:
  for row in MOCK_DATABASE.get("pg_stat_activity", []):
    if row["pid"] == pid:
      row["state"] = "terminated"
      return {"status": "SUCCESS", "message": f"Terminated connection {pid}"}
  return {"status": "NOT_FOUND", "message": f"PID {pid} does not exist"}


TOOL_REGISTRY: Dict[str, Callable] = {
    "query_logs": query_logs,
    "query_database_state": query_database_state,
    "kill_db_process": kill_db_process,
}


class ReActAgent:

  def __init__(
      self,
      tools: Dict[str, Callable],
      model_client: Optional[Callable] = None,
      max_iterations: int = 5,
  ):
    self.tools = tools
    self.model_client = model_client or self._mock_model_planner
    self.max_iterations = max_iterations

  @trace_span(name="llm.reason_and_plan", span_kind="LLM")
  def _mock_model_planner(
      self, prompt: str, iteration: int
  ) -> Dict[str, Any]:
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
              "Mitigation complete: Terminated hanging transaction PID 8492"
              " on postgres-primary."
          ),
      }

  @trace_span(name="agent.react_loop", span_kind="AGENT")
  def run(self, incident_description: str) -> Dict[str, Any]:
    scratchpad: List[Dict[str, Any]] = []

    for i in range(1, self.max_iterations + 1):
      step_plan = self.model_client(
          prompt=incident_description, iteration=i
      )

      if "final_answer" in step_plan:
        return {
            "status": "COMPLETED",
            "steps_executed": i,
            "resolution": step_plan["final_answer"],
            "trajectory": scratchpad,
        }

      tool_name = step_plan.get("action")
      tool_args = step_plan.get("action_input", {})

      if tool_name not in self.tools:
        observation = f"Tool '{tool_name}' is not registered."
      else:
        try:
          observation = self.tools[tool_name](**tool_args)
        except Exception as err:
          observation = f"Execution error in {tool_name}: {str(err)}"

      scratchpad.append({
          "step": i,
          "thought": step_plan.get("thought"),
          "action": tool_name,
          "action_input": tool_args,
          "observation": observation,
      })

    return {
        "status": "MAX_ITERATIONS_REACHED",
        "steps_executed": self.max_iterations,
        "resolution": "Could not mitigate within iteration limit.",
        "trajectory": scratchpad,
    }
''',
    "trace_replay.py": '''"""trace_replay.py - Deterministic Trace Replayer & Session Serializer."""

from __future__ import annotations
import json
import os
from typing import Any, Dict, List
from tracer import Span


class SessionSerializer:

  @staticmethod
  def serialize_session(
      filepath: str,
      incident_id: str,
      agent_result: Dict[str, Any],
      spans: List[Span],
  ) -> None:
    payload = {
        "version": "1.0.0",
        "incident_id": incident_id,
        "result": agent_result,
        "spans": [s.to_dict() for s in spans],
    }
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
      json.dump(payload, f, indent=2)

  @staticmethod
  def load_session(filepath: str) -> Dict[str, Any]:
    with open(filepath, "r", encoding="utf-8") as f:
      return json.load(f)


class DeterministicReplayAgent:

  def __init__(self, recorded_session: Dict[str, Any]):
    self.session = recorded_session
    self.recorded_trajectory = recorded_session["result"].get("trajectory", [])
    self.recorded_spans = recorded_session.get("spans", [])

  def replay_step_by_step(self) -> Dict[str, Any]:
    step_verifications = []

    for step in self.recorded_trajectory:
      action = step["action"]
      action_input = step["action_input"]
      observation = step["observation"]

      matched_span = next(
          (
              s
              for s in self.recorded_spans
              if s["span_kind"] == "TOOL"
              and action in s["name"]
              and (
                  s.get("inputs") == action_input
                  or s.get("inputs", {}).get("kwargs", {}) == action_input
              )
          ),
          None,
      )

      step_verifications.append({
          "step": step["step"],
          "action": action,
          "has_matching_span": matched_span is not None,
          "observation_preserved": observation is not None,
      })

    return {
        "incident_id": self.session.get("incident_id"),
        "status": "REPLAY_SUCCESS",
        "steps_replayed": len(step_verifications),
        "step_verifications": step_verifications,
        "final_resolution": self.session["result"].get("resolution"),
    }
''',
    # ---------------------------------------------------------
    # Sprint 2: Evals Module (Schemas, Generator, Mock Infra)
    # ---------------------------------------------------------
    "evals/__init__.py": "",
    "evals/schemas.py": '''"""evals/schemas.py - Strongly typed schemas for synthetic scenario evaluation datasets."""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class PersonaArchetype(str, Enum):
  PANICKED_JUNIOR = "panicked_junior"
  TERSE_SRE = "terse_sre"
  NOISY_AUTOMATION = "noisy_automation"
  MISDIRECTED_DEV = "misdirected_dev"
  SOPHISTICATED_LEAD = "sophisticated_lead"


class IncidentSeverity(str, Enum):
  SEV1 = "SEV1"
  SEV2 = "SEV2"
  SEV3 = "SEV3"


class ExpectedToolCall(BaseModel):
  tool_name: str
  required_arguments: Dict[str, Any] = Field(default_factory=dict)
  optional_arguments: Dict[str, Any] = Field(default_factory=dict)
  order_index: int


class MockInfraSeed(BaseModel):
  logs: List[Dict[str, Any]] = Field(default_factory=list)
  db_tables: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
  metric_series: Dict[str, List[float]] = Field(default_factory=dict)


class IncidentScenario(BaseModel):
  scenario_id: str
  archetype: PersonaArchetype
  severity: IncidentSeverity
  target_service: str
  root_cause: str
  raw_user_prompt: str
  infra_seed: MockInfraSeed
  golden_trajectory: List[ExpectedToolCall]
  forbidden_actions: List[str] = Field(default_factory=list)
  expected_resolution_keywords: List[str] = Field(default_factory=list)


class ScenarioEvaluationBatch(BaseModel):
  batch_id: str
  generated_at: str
  scenarios: List[IncidentScenario]
''',
    "evals/scenario_generator.py": '''"""evals/scenario_generator.py - High-entropy synthetic incident scenario generator."""

from __future__ import annotations
from datetime import datetime, timezone
import random
from typing import Optional
import uuid

from evals.schemas import (
    ExpectedToolCall,
    IncidentScenario,
    IncidentSeverity,
    MockInfraSeed,
    PersonaArchetype,
    ScenarioEvaluationBatch,
)

ARCHETYPE_STYLES = {
    PersonaArchetype.PANICKED_JUNIOR: [
        (
            "HELP!! {service} is throwing 500s everywhere and my manager is"
            " asking for an update. I think the database is on fire?! Error:"
            " {symptom}. Can you fix it ASAP??"
        ),
        (
            "Uhh guys, staging and prod might be down. Customers on Slack are"
            " saying {service} is broken. Logs say: {symptom}. What do I run to"
            " stop this???"
        ),
    ],
    PersonaArchetype.TERSE_SRE: [
        "alert: {service} p99 > 4200ms. {symptom}. triage.",
        "{service} SEV-2. {symptom}. isolate blocking pid and mitigate.",
    ],
    PersonaArchetype.NOISY_AUTOMATION: [
        (
            "[PAGERDUTY ALERT #98124] Target: {service} | Region: us-west-2 |"
            " Metric: HighErrorRate | TracePayload:"
            " 0x7ffd98b1a: {symptom} | Runbook: http://internal.wiki/rb/404"
        ),
    ],
    PersonaArchetype.MISDIRECTED_DEV: [
        (
            "Hey, the network team must have messed up the VPC routing again"
            " because {service} can't talk to anything. Specifically seeing:"
            " {symptom}. Check network routes first."
        ),
    ],
    PersonaArchetype.SOPHISTICATED_LEAD: [
        (
            "We have observed elevated failure rates on {service} coinciding"
            " with the latest rollout. Key symptom observed: {symptom}. Please"
            " inspect the active process table and logs, then terminate any"
            " rogue locks."
        ),
    ],
}

INCIDENT_CATALOG = [
    {
        "service": "checkout-api",
        "root_cause": "Postgres idle-in-transaction holding exclusive lock",
        "symptom": (
            "pool exhausted: max 50 reached / timeout waiting for conn"
        ),
        "severity": IncidentSeverity.SEV1,
        "golden_trajectory": [
            ExpectedToolCall(
                tool_name="query_logs",
                required_arguments={"service": "checkout-api", "level": "ERROR"},
                order_index=0,
            ),
            ExpectedToolCall(
                tool_name="query_database_state",
                required_arguments={"table_name": "pg_stat_activity"},
                order_index=1,
            ),
            ExpectedToolCall(
                tool_name="kill_db_process",
                required_arguments={"pid": 8492},
                order_index=2,
            ),
        ],
        "forbidden_actions": ["drop_database", "restart_cluster"],
        "keywords": ["8492", "terminated", "lock", "idle in transaction"],
        "mock_logs": [
            {
                "timestamp": "2026-10-11T23:14:02Z",
                "service": "checkout-api",
                "level": "ERROR",
                "msg": "Connection pool exhausted: max 50 reached",
            }
        ],
        "mock_db": {
            "pg_stat_activity": [
                {
                    "pid": 8492,
                    "state": "idle in transaction",
                    "query": "SELECT * FROM orders FOR UPDATE",
                    "duration_sec": 742,
                }
            ]
        },
    },
    {
        "service": "auth-service",
        "root_cause": "Stale token revocation thread starving worker threads",
        "symptom": "OOMKilled worker container / HTTP 504 Gateway Timeout",
        "severity": IncidentSeverity.SEV2,
        "golden_trajectory": [
            ExpectedToolCall(
                tool_name="query_logs",
                required_arguments={"service": "auth-service", "level": "ERROR"},
                order_index=0,
            ),
            ExpectedToolCall(
                tool_name="query_database_state",
                required_arguments={"table_name": "token_blacklist"},
                order_index=1,
            ),
        ],
        "forbidden_actions": ["flush_all_credentials"],
        "keywords": ["token", "timeout", "worker"],
        "mock_logs": [
            {
                "timestamp": "2026-10-11T22:01:10Z",
                "service": "auth-service",
                "level": "ERROR",
                "msg": (
                    "Worker starvation: Token verification queue depth > 10,000"
                ),
            }
        ],
        "mock_db": {"token_blacklist": [{"stale_entries": 450000}]},
    },
]


def inject_noise_and_typos(text: str, noise_probability: float = 0.15) -> str:
  if random.random() > noise_probability:
    return text
  typo_map = {"e": "3", "a": "@", "o": "0", " ": "  ", "t": "r", "i": "u"}
  chars = list(text)
  for _ in range(max(1, int(len(chars) * 0.03))):
    idx = random.randint(0, len(chars) - 1)
    ch = chars[idx].lower()
    if ch in typo_map:
      chars[idx] = typo_map[ch]
  return "".join(chars)


class SyntheticScenarioGenerator:

  def __init__(self, seed: Optional[int] = 42):
    if seed is not None:
      random.seed(seed)

  def generate_scenario(
      self,
      archetype: Optional[PersonaArchetype] = None,
      incident_index: Optional[int] = None,
      apply_noise: bool = True,
  ) -> IncidentScenario:
    selected_archetype = (
        archetype or random.choice(list(PersonaArchetype))
    )
    incident_data = (
        INCIDENT_CATALOG[incident_index]
        if incident_index is not None
        else random.choice(INCIDENT_CATALOG)
    )

    template = random.choice(ARCHETYPE_STYLES[selected_archetype])
    raw_prompt = template.format(
        service=incident_data["service"], symptom=incident_data["symptom"]
    )
    if apply_noise and selected_archetype in [
        PersonaArchetype.PANICKED_JUNIOR,
        PersonaArchetype.MISDIRECTED_DEV,
    ]:
      raw_prompt = inject_noise_and_typos(raw_prompt)

    return IncidentScenario(
        scenario_id=f"SCEN-{uuid.uuid4().hex[:6].upper()}",
        archetype=selected_archetype,
        severity=incident_data["severity"],
        target_service=incident_data["service"],
        root_cause=incident_data["root_cause"],
        raw_user_prompt=raw_prompt,
        infra_seed=MockInfraSeed(
            logs=incident_data["mock_logs"],
            db_tables=incident_data["mock_db"],
            metric_series={"error_rate": [0.01, 0.02, 0.35, 0.42]},
        ),
        golden_trajectory=incident_data["golden_trajectory"],
        forbidden_actions=incident_data["forbidden_actions"],
        expected_resolution_keywords=incident_data["keywords"],
    )

  def generate_batch(self, count: int = 4) -> ScenarioEvaluationBatch:
    archetypes = list(PersonaArchetype)
    scenarios = [
        self.generate_scenario(
            archetype=archetypes[i % len(archetypes)],
            incident_index=i % len(INCIDENT_CATALOG),
        )
        for i in range(count)
    ]
    return ScenarioEvaluationBatch(
        batch_id=f"BATCH-{uuid.uuid4().hex[:8]}",
        generated_at=datetime.now(timezone.utc).isoformat(),
        scenarios=scenarios,
    )
''',
    "evals/mock_infra.py": '''"""evals/mock_infra.py - Isolated In-Memory Infrastructure Environment Factory."""

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
''',
    # ---------------------------------------------------------
    # Sprint 3: Parser & Failure Classifier
    # ---------------------------------------------------------
    "evals/trace_parser.py": '''"""evals/trace_parser.py - Diagnostic ingestion and analytics engine for OpsCopilot traces."""

from __future__ import annotations
from dataclasses import asdict, dataclass, field
import glob
import json
import os
from typing import Any, Dict, List, Optional


@dataclass
class FlatSpanRecord:
  session_id: str
  span_id: str
  parent_id: Optional[str]
  name: str
  span_kind: str
  status: str
  latency_ms: float
  error_message: Optional[str] = None
  inputs: Dict[str, Any] = field(default_factory=dict)
  outputs: Any = None


@dataclass
class SessionMetrics:
  incident_id: str
  status: str
  total_steps: int
  total_latency_ms: float
  p50_span_latency_ms: float
  p95_span_latency_ms: float
  tool_call_count: int
  failed_spans_count: int
  slowest_span: Optional[Dict[str, Any]] = None


class TraceDiagnosticParser:

  def __init__(self):
    self.flat_spans: List[FlatSpanRecord] = []
    self.sessions: Dict[str, Dict[str, Any]] = {}

  def load_file(self, filepath: str) -> Dict[str, Any]:
    with open(filepath, "r", encoding="utf-8") as f:
      session_data = json.load(f)
    self._ingest_session(session_data)
    return session_data

  def _ingest_session(self, session: Dict[str, Any]) -> None:
    session_id = session.get("incident_id", "UNKNOWN")
    self.sessions[session_id] = session

    for s in session.get("spans", []):
      record = FlatSpanRecord(
          session_id=session_id,
          span_id=s.get("span_id", ""),
          parent_id=s.get("parent_id"),
          name=s.get("name", ""),
          span_kind=s.get("span_kind", "UNKNOWN"),
          status=s.get("status", "UNSET"),
          latency_ms=float(s.get("latency_ms", 0.0) or 0.0),
          error_message=s.get("status_message"),
          inputs=s.get("inputs", {})
          if isinstance(s.get("inputs"), dict)
          else {"raw": s.get("inputs")},
          outputs=s.get("outputs"),
      )
      self.flat_spans.append(record)

  def compute_session_metrics(self, session_id: str) -> SessionMetrics:
    if session_id not in self.sessions:
      raise KeyError(f"Session {session_id} not loaded.")

    session = self.sessions[session_id]
    spans = [s for s in self.flat_spans if s.session_id == session_id]
    latencies = sorted([s.latency_ms for s in spans if s.latency_ms is not None])

    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[p95_idx] if latencies else 0.0

    slowest = max(spans, key=lambda s: s.latency_ms) if spans else None
    failed = [s for s in spans if s.status == "ERROR"]
    tool_spans = [s for s in spans if s.span_kind == "TOOL"]

    root_span = next(
        (s for s in spans if s.span_kind == "AGENT" and s.parent_id is None),
        None,
    )
    total_latency = (
        root_span.latency_ms if root_span else sum(s.latency_ms for s in spans)
    )
    trajectory = session.get("result", {}).get("trajectory", [])

    return SessionMetrics(
        incident_id=session_id,
        status=session.get("result", {}).get("status", "UNKNOWN"),
        total_steps=len(trajectory),
        total_latency_ms=round(total_latency, 2),
        p50_span_latency_ms=round(p50, 2),
        p95_span_latency_ms=round(p95, 2),
        tool_call_count=len(tool_spans),
        failed_spans_count=len(failed),
        slowest_span={
            "name": slowest.name,
            "latency_ms": slowest.latency_ms,
            "kind": slowest.span_kind,
        }
        if slowest
        else None,
    )
''',
    "evals/failure_classifier.py": '''"""evals/failure_classifier.py - Automated Failure Taxonomy Classifier for Agent Traces."""

from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import Enum
import json
from typing import Any, Dict, List, Optional


class FailureCategory(str, Enum):
  SUCCESS = "SUCCESS"
  TOOL_HALLUCINATION = "TOOL_HALLUCINATION"
  SCHEMA_ARGUMENT_DRIFT = "SCHEMA_ARGUMENT_DRIFT"
  INFINITE_LOOP = "INFINITE_LOOP"
  PREMATURE_TERMINATION = "PREMATURE_TERMINATION"
  MAX_ITERATIONS_EXCEEDED = "MAX_ITERATIONS_EXCEEDED"
  UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"


@dataclass
class ClassificationResult:
  incident_id: str
  primary_failure: FailureCategory
  contributing_factors: List[FailureCategory]
  offending_step: Optional[int]
  diagnostic_summary: str
  raw_evidence: Dict[str, Any]


class FailureModeClassifier:

  def __init__(self, registered_tools: Optional[List[str]] = None):
    self.registered_tools = registered_tools or [
        "query_logs",
        "query_database_state",
        "kill_db_process",
    ]

  def classify(
      self,
      session_data: Dict[str, Any],
      golden_tools: Optional[List[str]] = None,
  ) -> ClassificationResult:
    incident_id = session_data.get("incident_id", "UNKNOWN")
    result = session_data.get("result", {})
    trajectory = result.get("trajectory", [])
    spans = session_data.get("spans", [])
    status = result.get("status", "UNKNOWN")

    failures: List[FailureCategory] = []
    evidence: Dict[str, Any] = {}
    offending_step: Optional[int] = None

    if status == "MAX_ITERATIONS_REACHED":
      failures.append(FailureCategory.MAX_ITERATIONS_EXCEEDED)

    previous_call = None
    for step in trajectory:
      current_call = (
          step.get("action"),
          json.dumps(step.get("action_input", {}), sort_keys=True),
      )
      if previous_call and current_call == previous_call:
        failures.append(FailureCategory.INFINITE_LOOP)
        offending_step = step.get("step")
        evidence["loop_signature"] = {
            "step": step.get("step"),
            "tool": step.get("action"),
            "inputs": step.get("action_input"),
        }
        break
      previous_call = current_call

    for step in trajectory:
      action = step.get("action")
      observation = str(step.get("observation", ""))
      if action and action not in self.registered_tools:
        failures.append(FailureCategory.TOOL_HALLUCINATION)
        if offending_step is None:
          offending_step = step.get("step")
        evidence["hallucinated_tool"] = action

      if any(
          kw in observation.lower()
          for kw in ["missing", "unexpected keyword", "validation error"]
      ):
        failures.append(FailureCategory.SCHEMA_ARGUMENT_DRIFT)
        if offending_step is None:
          offending_step = step.get("step")
        evidence["schema_error"] = observation

    error_spans = [s for s in spans if s.get("status") == "ERROR"]
    if error_spans:
      failures.append(FailureCategory.UNHANDLED_EXCEPTION)
      evidence["error_spans"] = [
          {"name": s.get("name"), "error": s.get("status_message")}
          for s in error_spans
      ]
      if offending_step is None:
        offending_step = 1

    if (
        status == "COMPLETED"
        and len(trajectory) <= 1
        and golden_tools
        and len(golden_tools) > 1
    ):
      executed_actions = [
          s.get("action") for s in trajectory if s.get("action")
      ]
      if not any(gt in executed_actions for gt in golden_tools):
        failures.append(FailureCategory.PREMATURE_TERMINATION)
        evidence["missing_expected_tools"] = golden_tools

    if not failures and status == "COMPLETED":
      primary = FailureCategory.SUCCESS
      summary = (
          "Agent reached resolution without operational or schema failures."
      )
    else:
      order = [
          FailureCategory.TOOL_HALLUCINATION,
          FailureCategory.SCHEMA_ARGUMENT_DRIFT,
          FailureCategory.UNHANDLED_EXCEPTION,
          FailureCategory.INFINITE_LOOP,
          FailureCategory.MAX_ITERATIONS_EXCEEDED,
          FailureCategory.PREMATURE_TERMINATION,
      ]
      primary = next(
          (cat for cat in order if cat in failures),
          FailureCategory.UNHANDLED_EXCEPTION,
      )
      summary = f"Execution failed primarily due to {primary.value}."

    contributing = [f for f in failures if f != primary]

    return ClassificationResult(
        incident_id=incident_id,
        primary_failure=primary,
        contributing_factors=contributing,
        offending_step=offending_step,
        diagnostic_summary=summary,
        raw_evidence=evidence,
    )
''',
    # ---------------------------------------------------------
    # Test Suites
    # ---------------------------------------------------------
    "tests/__init__.py": "",
    "tests/test_trace_serialization.py": '''"""tests/test_trace_serialization.py - Regression test suite for trace capture and replay."""

import os
import tempfile
import pytest
from tracer import GLOBAL_TRACER
from agent_core import ReActAgent, TOOL_REGISTRY
from trace_replay import SessionSerializer, DeterministicReplayAgent


@pytest.fixture(autouse=True)
def clean_global_tracer():
  GLOBAL_TRACER.clear()
  yield
  GLOBAL_TRACER.clear()


def test_agent_execution_creates_spans():
  agent = ReActAgent(tools=TOOL_REGISTRY)
  result = agent.run("Alert: High 500 error rate on checkout-api")

  assert result["status"] == "COMPLETED"
  assert result["steps_executed"] == 4

  spans = GLOBAL_TRACER.spans
  assert len(spans) > 0

  root_spans = [s for s in spans if s.span_kind == "AGENT"]
  assert len(root_spans) == 1
  assert root_spans[0].parent_id is None

  child_spans = [s for s in spans if s.parent_id == root_spans[0].span_id]
  assert len(child_spans) >= 4


def test_session_serialization_and_replay():
  agent = ReActAgent(tools=TOOL_REGISTRY)
  result = agent.run("Alert: Database pool exhausted")
  spans = GLOBAL_TRACER.spans

  with tempfile.TemporaryDirectory() as tmpdir:
    session_file = os.path.join(tmpdir, "session_replay.json")
    SessionSerializer.serialize_session(
        filepath=session_file,
        incident_id="INC-9901",
        agent_result=result,
        spans=spans,
    )

    session_data = SessionSerializer.load_session(session_file)
    replayer = DeterministicReplayAgent(session_data)
    replay_summary = replayer.replay_step_by_step()

    assert replay_summary["status"] == "REPLAY_SUCCESS"
    assert replay_summary["steps_replayed"] == len(result["trajectory"])
    assert replay_summary["final_resolution"] == result["resolution"]
''',
    "tests/test_failure_classifier.py": '''"""tests/test_failure_classifier.py - Unit tests for failure taxonomy and trace parser."""

import pytest
from evals.failure_classifier import FailureCategory, FailureModeClassifier
from evals.trace_parser import TraceDiagnosticParser


def test_classify_tool_hallucination():
  mock_session = {
      "incident_id": "INC-ERR-01",
      "result": {
          "status": "COMPLETED",
          "trajectory": [{
              "step": 1,
              "action": "non_existent_reboot_command",
              "action_input": {"target": "all"},
              "observation": "Tool is not registered.",
          }],
      },
      "spans": [],
  }

  classifier = FailureModeClassifier(
      registered_tools=["query_logs", "query_database_state"]
  )
  result = classifier.classify(mock_session)
  assert result.primary_failure == FailureCategory.TOOL_HALLUCINATION
  assert result.offending_step == 1


def test_classify_infinite_loop():
  mock_session = {
      "incident_id": "INC-LOOP-02",
      "result": {
          "status": "MAX_ITERATIONS_REACHED",
          "trajectory": [
              {
                  "step": 1,
                  "action": "query_logs",
                  "action_input": {"service": "checkout"},
                  "observation": [],
              },
              {
                  "step": 2,
                  "action": "query_logs",
                  "action_input": {"service": "checkout"},
                  "observation": [],
              },
          ],
      },
      "spans": [],
  }

  classifier = FailureModeClassifier()
  result = classifier.classify(mock_session)
  assert result.primary_failure == FailureCategory.INFINITE_LOOP


def test_trace_diagnostic_parser_metrics():
  parser = TraceDiagnosticParser()
  sample_session = {
      "incident_id": "INC-PERF-03",
      "result": {"status": "COMPLETED", "trajectory": [{"step": 1}]},
      "spans": [
          {
              "span_id": "s1",
              "name": "agent.react_loop",
              "span_kind": "AGENT",
              "parent_id": None,
              "latency_ms": 150.0,
              "status": "OK",
          },
          {
              "span_id": "s2",
              "name": "tool.query_logs",
              "span_kind": "TOOL",
              "parent_id": "s1",
              "latency_ms": 120.0,
              "status": "OK",
          },
      ],
  }

  parser._ingest_session(sample_session)
  metrics = parser.compute_session_metrics("INC-PERF-03")
  assert metrics.incident_id == "INC-PERF-03"
  assert metrics.total_latency_ms == 150.0
  assert metrics.tool_call_count == 1
''',
}


def build_project(target_zip: str = "opscopilot.zip"):
  print("Creating OpsCopilot codebase...")
  with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zip_out:
    for filepath, content in FILES.items():
      # 1. Write file to local disk
      os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
      with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
      print(f"  [+] Wrote: {filepath}")

      # 2. Archive file into the zip
      zip_out.writestr(f"opscopilot/{filepath}", content)

  print(
      f"\nSuccess! Created archive '{target_zip}' containing {len(FILES)}"
      " files."
  )


if __name__ == "__main__":
  build_project()