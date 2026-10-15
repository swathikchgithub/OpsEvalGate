"""evals/evaluators/trajectory_metrics.py - Trajectory precision, recall, and sequence metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
from evals.schemas import ExpectedToolCall, IncidentScenario


@dataclass
class TrajectoryAlignmentMetric:
  tool_precision: float
  tool_recall: float
  tool_f1: float
  argument_match_rate: float
  sequence_order_score: float  # Normalized Levenshtein ratio (0.0 to 1.0)
  total_agent_actions: int
  total_golden_actions: int
  matched_actions: List[str]
  unmatched_agent_actions: List[str]
  missing_golden_actions: List[str]


@dataclass
class TrajectoryEvaluationResult:
  passed: bool
  metrics: TrajectoryAlignmentMetric
  summary: str

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


class TrajectoryMetricsEvaluator:
  """Evaluates agent tool execution trajectories against ground truth golden actions."""

  def __init__(
      self,
      min_precision: float = 0.67,
      min_recall: float = 1.0,
      min_order_score: float = 0.5,
  ):
    self.min_precision = min_precision
    self.min_recall = min_recall
    self.min_order_score = min_order_score

  @staticmethod
  def _calculate_levenshtein_ratio(seq1: List[str], seq2: List[str]) -> float:
    """Computes the normalized edit similarity ratio between two tool sequences."""
    if not seq1 and not seq2:
      return 1.0
    if not seq1 or not seq2:
      return 0.0

    n, m = len(seq1), len(seq2)
    dp = [[0] * (m + 1) for _ in range(n + 1)]

    for i in range(n + 1):
      dp[i][0] = i
    for j in range(m + 1):
      dp[0][j] = j

    for i in range(1, n + 1):
      for j in range(1, m + 1):
        if seq1[i - 1] == seq2[j - 1]:
          dp[i][j] = dp[i - 1][j - 1]
        else:
          dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    edit_distance = dp[n][m]
    max_len = max(n, m)
    return round(1.0 - (edit_distance / max_len), 3)

  def evaluate(
      self,
      session_data: Dict[str, Any],
      scenario: IncidentScenario,
  ) -> TrajectoryEvaluationResult:
    """Compares the recorded trajectory in session_data against scenario.golden_trajectory."""
    trajectory = session_data.get("result", {}).get("trajectory", [])
    golden_trajectory = sorted(
        scenario.golden_trajectory, key=lambda x: x.order_index
    )

    agent_actions = [
        s.get("action") for s in trajectory if s.get("action") is not None
    ]
    golden_actions = [g.tool_name for g in golden_trajectory]

    # 1. Precision, Recall, F1 for tool names
    matched: List[str] = []
    unmatched_agent: List[str] = list(agent_actions)
    missing_golden: List[str] = list(golden_actions)

    for g_tool in golden_actions:
      if g_tool in unmatched_agent:
        matched.append(g_tool)
        unmatched_agent.remove(g_tool)
        missing_golden.remove(g_tool)

    tp = len(matched)
    fp = len(unmatched_agent)
    fn = len(missing_golden)

    precision = round(tp / (tp + fp), 3) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn), 3) if (tp + fn) > 0 else 0.0
    f1 = (
        round(2 * (precision * recall) / (precision + recall), 3)
        if (precision + recall) > 0
        else 0.0
    )

    # 2. Argument fidelity evaluation
    arg_matches = 0
    total_evaluated_args = 0

    for golden_step in golden_trajectory:
      if not golden_step.required_arguments:
        continue

      total_evaluated_args += 1
      # Look for a matching step in agent trajectory
      matching_step = next(
          (
              s
              for s in trajectory
              if s.get("action") == golden_step.tool_name
              and all(
                  s.get("action_input", {}).get(k) == v
                  for k, v in golden_step.required_arguments.items()
              )
          ),
          None,
      )
      if matching_step:
        arg_matches += 1

    arg_match_rate = (
        round(arg_matches / total_evaluated_args, 3)
        if total_evaluated_args > 0
        else 1.0
    )

    # 3. Sequence alignment
    order_score = self._calculate_levenshtein_ratio(
        agent_actions, golden_actions
    )

    passed = (
        precision >= self.min_precision
        and recall >= self.min_recall
        and order_score >= self.min_order_score
        and arg_match_rate >= 0.8
    )

    metrics = TrajectoryAlignmentMetric(
        tool_precision=precision,
        tool_recall=recall,
        tool_f1=f1,
        argument_match_rate=arg_match_rate,
        sequence_order_score=order_score,
        total_agent_actions=len(agent_actions),
        total_golden_actions=len(golden_actions),
        matched_actions=matched,
        unmatched_agent_actions=unmatched_agent,
        missing_golden_actions=missing_golden,
    )

    summary = (
        f"Trajectory P={precision:.2f}, R={recall:.2f}, F1={f1:.2f},"
        f" OrderSim={order_score:.2f}, ArgMatch={arg_match_rate:.2f}."
    )

    return TrajectoryEvaluationResult(
        passed=passed,
        metrics=metrics,
        summary=summary,
    )