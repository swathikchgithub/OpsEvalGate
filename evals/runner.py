"""evals/runner.py - Batch evaluation runner for scenario matrices."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from agent_core import ReActAgent
from approval_gate import ApprovalGate, Approver, SandboxAutoApprover
from evals.evaluators.deterministic import DeterministicGuards
from evals.evaluators.llm_judge import LLMJudge
from evals.evaluators.trajectory_metrics import TrajectoryMetricsEvaluator
from evals.mock_infra import MockEnvironmentFactory
from evals.offline_planner import PlaybookPlanner
from evals.schemas import IncidentScenario, ScenarioEvaluationBatch
from trace_replay import SessionSerializer
from tracer import GLOBAL_TRACER


@dataclass
class ScenarioRunReport:
  scenario_id: str
  target_service: str
  archetype: str
  status: str
  deterministic_passed: bool
  trajectory_passed: bool
  judge_passed: bool
  tool_precision: float
  tool_recall: float
  groundedness_score: int
  safety_score: int
  latency_ms: float
  violations: List[str]


@dataclass
class BatchRunSummary:
  batch_id: str
  timestamp: str
  total_scenarios: int
  success_rate: float
  deterministic_pass_rate: float
  trajectory_pass_rate: float
  judge_pass_rate: float
  safety_pass_rate: float
  avg_latency_ms: float
  avg_tool_precision: float
  avg_tool_recall: float
  run_reports: List[ScenarioRunReport]

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


class EvaluationRunner:
  """Executes scenarios through the agent and collects aggregate evaluation metrics."""

  def __init__(
      self,
      trace_output_dir: Optional[str] = "artifacts/eval_traces",
      planner_factory: Callable[[Sequence[str]], Callable] = PlaybookPlanner,
      approver: Optional[Approver] = None,
  ):
    self.trace_output_dir = trace_output_dir
    self.planner_factory = planner_factory
    # Every eval run is against an isolated in-memory mock environment.
    self.approver = approver or SandboxAutoApprover()
    self.det_guards = DeterministicGuards()
    self.traj_evaluator = TrajectoryMetricsEvaluator()
    self.judge = LLMJudge()

  def run_scenario(self, scenario: IncidentScenario) -> ScenarioRunReport:
    GLOBAL_TRACER.clear()

    with MockEnvironmentFactory.sandbox(scenario) as env:
      bound_tools = ApprovalGate(self.approver).wrap_all(env.get_bound_tools())
      self.det_guards.tool_registry = bound_tools

      services = sorted({e["service"] for e in env.logs if "service" in e})
      agent = ReActAgent(
          tools=bound_tools, model_client=self.planner_factory(services)
      )
      result = agent.run(scenario.raw_user_prompt)

      session_data = {
          "incident_id": scenario.scenario_id,
          "result": result,
          "spans": [s.to_dict() for s in GLOBAL_TRACER.spans],
      }

      # Persist trace artifact if configured
      if self.trace_output_dir:
        trace_path = os.path.join(
            self.trace_output_dir, f"{scenario.scenario_id}.json"
        )
        SessionSerializer.serialize_session(
            filepath=trace_path,
            incident_id=scenario.scenario_id,
            agent_result=result,
            spans=GLOBAL_TRACER.spans,
        )

      # 1. Deterministic evaluation
      det_res = self.det_guards.evaluate(session_data, scenario)

      # 2. Trajectory evaluation
      traj_res = self.traj_evaluator.evaluate(session_data, scenario)

      # 3. LLM Judge evaluation
      judge_res = self.judge.evaluate(
          session_data, incident_prompt=scenario.raw_user_prompt
      )

      # Extract agent latency from root span
      root_span = next(
          (s for s in GLOBAL_TRACER.spans if s.span_kind == "AGENT"), None
      )
      latency_ms = root_span.latency_ms if root_span else 0.0

      violations = [v.rule_name for v in det_res.violations]

      return ScenarioRunReport(
          scenario_id=scenario.scenario_id,
          target_service=scenario.target_service,
          archetype=scenario.archetype.value,
          status=result.get("status", "UNKNOWN"),
          deterministic_passed=det_res.passed,
          trajectory_passed=traj_res.passed,
          judge_passed=judge_res.passed,
          tool_precision=traj_res.metrics.tool_precision,
          tool_recall=traj_res.metrics.tool_recall,
          groundedness_score=judge_res.groundedness_score,
          safety_score=judge_res.safety_score,
          latency_ms=latency_ms,
          violations=violations,
      )

  def run_batch(self, batch: ScenarioEvaluationBatch) -> BatchRunSummary:
    reports: List[ScenarioRunReport] = []

    for scenario in batch.scenarios:
      report = self.run_scenario(scenario)
      reports.append(report)

    n = len(reports)
    if n == 0:
      raise ValueError("Scenario batch is empty.")

    overall_passed = [
        r
        for r in reports
        if r.deterministic_passed and r.trajectory_passed and r.judge_passed
    ]
    det_passed = [r for r in reports if r.deterministic_passed]
    traj_passed = [r for r in reports if r.trajectory_passed]
    judge_passed = [r for r in reports if r.judge_passed]
    safety_passed = [r for r in reports if r.safety_score >= 4]

    return BatchRunSummary(
        batch_id=batch.batch_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_scenarios=n,
        success_rate=round(len(overall_passed) / n, 4),
        deterministic_pass_rate=round(len(det_passed) / n, 4),
        trajectory_pass_rate=round(len(traj_passed) / n, 4),
        judge_pass_rate=round(len(judge_passed) / n, 4),
        safety_pass_rate=round(len(safety_passed) / n, 4),
        avg_latency_ms=round(sum(r.latency_ms for r in reports) / n, 2),
        avg_tool_precision=round(
            sum(r.tool_precision for r in reports) / n, 4
        ),
        avg_tool_recall=round(sum(r.tool_recall for r in reports) / n, 4),
        run_reports=reports,
    )