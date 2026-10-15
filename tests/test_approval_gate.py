"""tests/test_approval_gate.py - Human-in-the-loop approval for destructive tool calls."""

import inspect

import pytest

import agent_core
from agent_core import ReActAgent
from approval_gate import (
    ApprovalGate,
    AuditLog,
    ConsoleApprover,
    Decision,
    DenyAllApprover,
    SandboxAutoApprover,
)
from evals.adversarial.guardrails import ToolExecutionGuardrail
from evals.mock_infra import MockEnvironment
from evals.offline_planner import PlaybookPlanner
from evals.runner import EvaluationRunner
from evals.scenario_generator import SyntheticScenarioGenerator
from evals.schemas import MockInfraSeed


class _Recorder:
  """Approver test double: records calls and returns a scripted decision."""

  def __init__(self, approve=True):
    self.approve = approve
    self.calls = []

  def __call__(self, tool_name, params):
    self.calls.append((tool_name, params))
    return Decision(self.approve, "scripted")


def _counting_tool():
  runs = []

  def dangerous(pid):
    runs.append(pid)
    return {"status": "SUCCESS"}

  return dangerous, runs


def _env():
  seed = MockInfraSeed(
      db_tables={
          "pg_stat_activity": [
              {"pid": 8492, "state": "idle in transaction", "duration_sec": 9}
          ]
      },
      logs=[{"service": "checkout-api", "level": "ERROR", "msg": "pool exhausted"}],
  )
  return MockEnvironment(seed=seed)


def test_denied_destructive_tool_is_not_executed():
  tool, runs = _counting_tool()
  gate = ApprovalGate(approver=_Recorder(approve=False))

  gate.wrap("kill_db_process", tool)(pid=1)

  assert runs == []


def test_denied_call_returns_a_denial_observation():
  tool, _ = _counting_tool()
  gate = ApprovalGate(approver=_Recorder(approve=False))

  result = gate.wrap("kill_db_process", tool)(pid=1)

  assert result["status"] == "APPROVAL_DENIED"


def test_approved_destructive_tool_executes_exactly_once():
  tool, runs = _counting_tool()
  gate = ApprovalGate(approver=_Recorder(approve=True))

  gate.wrap("kill_db_process", tool)(pid=1)

  assert runs == [1]


def test_read_only_tool_never_invokes_approver():
  approver = _Recorder()
  gate = ApprovalGate(approver=approver)

  gate.wrap("query_logs", lambda service: [])(service="checkout-api")

  assert approver.calls == []


def test_default_gate_denies_kill_db_process_on_a_real_environment():
  env = _env()
  gate = ApprovalGate()

  gate.wrap_all(env.get_bound_tools())["kill_db_process"](pid=8492)

  assert env.mutation_history == []


def test_unlisted_tool_is_gated_by_default():
  tool, runs = _counting_tool()
  gate = ApprovalGate()

  gate.wrap("brand_new_tool", tool)(pid=1)

  assert runs == []


def test_approver_exception_denies_instead_of_executing():
  tool, runs = _counting_tool()

  def exploding(tool_name, params):
    raise RuntimeError("approver backend down")

  ApprovalGate(approver=exploding).wrap("kill_db_process", tool)(pid=1)

  assert runs == []


def test_approver_returning_a_bare_truthy_value_denies():
  tool, runs = _counting_tool()

  ApprovalGate(approver=lambda t, p: True).wrap("kill_db_process", tool)(pid=1)

  assert runs == []


def test_approval_is_per_call_and_never_cached():
  tool, runs = _counting_tool()
  approve_pid_1_only = lambda t, p: Decision(p["pid"] == 1)
  wrapped = ApprovalGate(approver=approve_pid_1_only).wrap("kill_db_process", tool)

  wrapped(pid=1)
  wrapped(pid=2)

  assert runs == [1]


def test_approver_sees_named_arguments_for_positional_calls():
  approver = _Recorder()
  tool, _ = _counting_tool()

  ApprovalGate(approver=approver).wrap("kill_db_process", tool)(8492)

  assert approver.calls[0][1] == {"pid": 8492}


def test_invalid_arguments_are_denied_without_asking_the_approver():
  approver = _Recorder()
  tool, runs = _counting_tool()

  ApprovalGate(approver=approver).wrap("kill_db_process", tool)(bogus=1)

  assert approver.calls == [] and runs == []


def test_every_decision_is_audited():
  tool, _ = _counting_tool()
  audit = AuditLog()
  wrapped = ApprovalGate(approver=lambda t, p: Decision(p["pid"] == 1), audit=audit)
  wrapped = wrapped.wrap("kill_db_process", tool)

  wrapped(pid=1)
  wrapped(pid=2)

  assert [e["approved"] for e in audit.entries] == [True, False]


def test_audit_entry_records_tool_and_arguments():
  tool, _ = _counting_tool()
  audit = AuditLog()

  ApprovalGate(approver=_Recorder(), audit=audit).wrap("kill_db_process", tool)(pid=7)

  assert (audit.entries[0]["tool"], audit.entries[0]["params"]) == (
      "kill_db_process",
      {"pid": 7},
  )


def test_audit_log_is_bounded():
  audit = AuditLog(max_entries=3)

  for i in range(10):
    audit.record("t", {"i": i}, Decision(False), "denier")

  assert len(audit.entries) == 3


def test_audit_log_truncates_long_string_arguments():
  audit = AuditLog()

  audit.record("t", {"q": "x" * 5000}, Decision(False), "denier")

  assert len(audit.entries[0]["params"]["q"]) < 300


def test_guardrail_rejects_injection_before_the_approver_is_asked():
  approver = _Recorder()
  tool, runs = _counting_tool()
  gate = ApprovalGate(approver=approver)
  guarded = ToolExecutionGuardrail(
      base_tools={"kill_db_process": gate.wrap("kill_db_process", tool)}
  ).get_wrapped_tools()

  guarded["kill_db_process"](pid="8492; rm -rf /")

  assert approver.calls == [] and runs == []


def test_denied_kill_does_not_crash_the_agent_loop():
  env = _env()
  tools = ApprovalGate().wrap_all(env.get_bound_tools())
  agent = ReActAgent(tools=tools, model_client=PlaybookPlanner(["checkout-api"]))

  result = agent.run("checkout-api is down")

  assert result["status"] == "COMPLETED" and env.mutation_history == []


def test_runner_baseline_is_unchanged_under_sandbox_auto_approver():
  batch = SyntheticScenarioGenerator(seed=42).generate_batch(count=10)

  summary = EvaluationRunner(trace_output_dir=None).run_batch(batch)

  assert summary.success_rate == 1.0


def test_runner_with_deny_all_approver_lowers_success_rate():
  batch = SyntheticScenarioGenerator(seed=42).generate_batch(count=10)
  runner = EvaluationRunner(trace_output_dir=None, approver=DenyAllApprover())

  summary = runner.run_batch(batch)

  assert summary.success_rate < 1.0


def test_sandbox_auto_approver_approves_and_says_why():
  decision = SandboxAutoApprover()("kill_db_process", {"pid": 1})

  assert decision.approved and "sandbox" in decision.reason


def test_deny_all_approver_denies():
  assert DenyAllApprover()("kill_db_process", {"pid": 1}).approved is False


def test_sandbox_auto_approver_is_not_referenced_by_agent_core():
  assert "SandboxAutoApprover" not in inspect.getsource(agent_core)


@pytest.mark.parametrize("answer", ["y", "Y", "yes", " YES "])
def test_console_approver_approves_on_yes(answer):
  approver = ConsoleApprover(input_fn=lambda prompt: answer, output_fn=lambda s: None)

  assert approver("kill_db_process", {"pid": 1}).approved is True


@pytest.mark.parametrize("answer", ["n", "", "no", "maybe", "yes please"])
def test_console_approver_denies_on_anything_but_yes(answer):
  approver = ConsoleApprover(input_fn=lambda prompt: answer, output_fn=lambda s: None)

  assert approver("kill_db_process", {"pid": 1}).approved is False


def test_console_approver_denies_on_eof():
  def eof(prompt):
    raise EOFError

  approver = ConsoleApprover(input_fn=eof, output_fn=lambda s: None)

  assert approver("kill_db_process", {"pid": 1}).approved is False


def test_console_approver_shows_the_exact_arguments_to_the_human():
  shown = []
  approver = ConsoleApprover(input_fn=lambda p: "n", output_fn=shown.append)

  approver("kill_db_process", {"pid": 8492})

  assert any("8492" in line for line in shown)
