"""approval_gate.py - Human-in-the-loop approval for destructive tool calls.

Fail-closed: only tools explicitly listed as read-only run without approval.
Anything else (including tools added later) needs an explicit `Decision(True)`.
Approval is per call on the exact arguments; nothing is cached or reused.
"""

from __future__ import annotations

import functools
import inspect
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

DEFAULT_READ_ONLY_TOOLS = frozenset({"query_logs", "query_database_state"})
MAX_AUDIT_ENTRIES = 1000
MAX_AUDIT_STRING = 200

Params = Dict[str, Any]


@dataclass(frozen=True)
class Decision:
  approved: bool
  reason: str = ""


Approver = Callable[[str, Params], Decision]


class DenyAllApprover:
  """Default policy: nothing destructive runs unless a real approver is wired in."""

  def __call__(self, tool_name: str, params: Params) -> Decision:
    return Decision(False, "deny-all policy: no approver configured")


class SandboxAutoApprover:
  """Approves every call. Only for runs against an isolated in-memory mock
  environment (evals/CI); never wire this into a live agent."""

  def __call__(self, tool_name: str, params: Params) -> Decision:
    return Decision(True, "auto-approved: isolated mock sandbox")


class ConsoleApprover:
  """Asks a human on the terminal. Anything but an explicit yes is a denial."""

  def __init__(
      self,
      input_fn: Callable[[str], str] = input,
      output_fn: Callable[[str], None] = print,
  ):
    self.input_fn = input_fn
    self.output_fn = output_fn

  def __call__(self, tool_name: str, params: Params) -> Decision:
    self.output_fn(f"Agent requests destructive action: {tool_name}({params})")
    try:
      answer = self.input_fn("Approve? [y/N]: ")
    except EOFError:
      return Decision(False, "no input available")
    if answer.strip().lower() in ("y", "yes"):
      return Decision(True, "approved by human")
    return Decision(False, "denied by human")


class AuditLog:
  """Bounded record of every approval decision (OWASP A09)."""

  def __init__(self, max_entries: int = MAX_AUDIT_ENTRIES):
    self._entries: Deque[Dict[str, Any]] = deque(maxlen=max_entries)

  @property
  def entries(self) -> List[Dict[str, Any]]:
    return list(self._entries)

  def record(
      self, tool: str, params: Params, decision: Decision, approver: str
  ) -> None:
    self._entries.append({
        "tool": tool,
        "params": {k: _truncate(v) for k, v in params.items()},
        "approved": decision.approved,
        "reason": decision.reason,
        "approver": approver,
    })


def _truncate(value: Any) -> Any:
  if isinstance(value, str) and len(value) > MAX_AUDIT_STRING:
    return value[:MAX_AUDIT_STRING] + "..."
  return value


class ApprovalGate:
  """Decorator that asks an Approver before any non-read-only tool runs."""

  def __init__(
      self,
      approver: Optional[Approver] = None,
      read_only_tools: Iterable[str] = DEFAULT_READ_ONLY_TOOLS,
      audit: Optional[AuditLog] = None,
  ):
    self.approver = approver or DenyAllApprover()
    self.read_only_tools = frozenset(read_only_tools)
    self.audit = audit if audit is not None else AuditLog()

  def wrap(self, name: str, fn: Callable) -> Callable:
    if name in self.read_only_tools:
      return fn

    @functools.wraps(fn)
    def gated(*args: Any, **kwargs: Any) -> Any:
      try:
        params = dict(inspect.signature(fn).bind(*args, **kwargs).arguments)
      except TypeError:
        return _denied("invalid arguments")
      decision = self._decide(name, params)
      self.audit.record(name, params, decision, type(self.approver).__name__)
      if not decision.approved:
        return _denied(decision.reason)
      return fn(*args, **kwargs)

    return gated

  def wrap_all(self, tools: Dict[str, Callable]) -> Dict[str, Callable]:
    return {name: self.wrap(name, fn) for name, fn in tools.items()}

  def _decide(self, name: str, params: Params) -> Decision:
    try:
      decision = self.approver(name, params)
    except Exception as exc:
      return Decision(False, f"approver error: {type(exc).__name__}")
    if not isinstance(decision, Decision):
      return Decision(False, "approver returned an invalid decision")
    return decision


def _denied(reason: str) -> Dict[str, str]:
  return {"status": "APPROVAL_DENIED", "reason": reason}
