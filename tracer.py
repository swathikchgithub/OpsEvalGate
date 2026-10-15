"""tracer.py - OpenInference-compatible tracing engine for OpsEvalGate."""

from __future__ import annotations

import contextvars
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import functools
import json
import time
from typing import Any, Callable, Dict, List, Optional
import uuid
import os
from pathlib import Path 

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

  import os
from pathlib import Path


def load_dotenv(env_path: str = ".env") -> None:
  """Reads key-value pairs from .env and populates os.environ without overriding existing vars."""
  path = Path(env_path)
  if not path.is_file():
    return

  with open(path, "r", encoding="utf-8") as f:
    for line in f:
      line = line.strip()
      # Skip empty lines and comment lines
      if not line or line.startswith("#") or "=" not in line:
        continue

      key, val = line.split("=", 1)
      key = key.strip()
      val = val.strip().strip("'\"")

      # Only populate if not already set in the current process
      if key not in os.environ:
        os.environ[key] = val
