"""evals/trace_parser.py - Diagnostic ingestion and analytics engine for OpsEvalGate traces."""

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
