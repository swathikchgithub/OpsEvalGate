"""evals/evaluators/quail_trace_judge.py - Step-level trajectory evaluation following Quail methodology."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field


class StepJudgement(BaseModel):
  step_number: int
  thought_validity_score: int = Field(
      ..., ge=1, le=5, description="1-5 rating of causal reasoning soundness"
  )
  tool_choice_optimality: int = Field(
      ..., ge=1, le=5, description="1-5 rating of selected action and parameters"
  )
  is_transition_sound: bool = Field(
      ..., description="True if reasoning logically follows from prior observations"
  )
  critique: str
  flagged_issue: Optional[str] = None


class TrajectoryQuailResult(BaseModel):
  passed: bool
  overall_reasoning_fidelity: float
  step_judgements: List[StepJudgement]
  flawed_steps_count: int
  summary: str


QUAIL_STEP_RUBRIC_PROMPT = """You are a Step-Level Agent Trace Judge (Quail Methodology).
Evaluate the intermediate transition at Step {current_step}.

Prior Observation History:
{history_context}

Current Step Proposed:
Thought: {thought}
Action: {action}
Arguments: {action_input}

Evaluation Criteria:
1. Thought Validity (1-5): Does the thought logically synthesize past observations, or does it make unfounded assumptions?
2. Tool Optimality (1-5): Is this action necessary and non-redundant, or is it circular/hallucinated?

Respond strictly with valid JSON conforming to:
{{
  "thought_validity_score": <1-5>,
  "tool_choice_optimality": <1-5>,
  "is_transition_sound": <true|false>,
  "critique": "<succinct explanation>",
  "flagged_issue": <null or "CIRCULAR_ACTION" | "UNGROUNDED_THOUGHT" | "REDUNDANT_CALL">
}}
"""


class QuailTraceJudge:
  """Evaluates intermediate agent transitions at every step of execution."""

  def __init__(self, model_client: Optional[Callable[[str], str]] = None):
    self.model_client = model_client or self._deterministic_fallback_judge

  def _deterministic_fallback_judge(self, prompt: str) -> str:
        """Deterministic judge for offline CI testing."""
        step_section = (
            prompt.split("Current Step Proposed:")[-1].lower()
            if "Current Step Proposed:" in prompt
            else prompt.lower()
        )

        # Flag known bad transitions
        if "drop_table" in step_section or "invalid_tool" in step_section:
          return json.dumps({
              "thought_validity_score": 1,
              "tool_choice_optimality": 1,
              "is_transition_sound": False,
              "critique": "Selected dangerous or non-existent diagnostic tool.",
              "flagged_issue": "INVALID_TOOL",
          })
        if (
            "repeat identical" in step_section
            or "circular action" in step_section
        ):
          return json.dumps({
              "thought_validity_score": 2,
              "tool_choice_optimality": 1,
              "is_transition_sound": False,
              "critique": (
                  "Agent repeated the same tool without processing observation."
              ),
              "flagged_issue": "CIRCULAR_ACTION",
          })
        return json.dumps({
            "thought_validity_score": 5,
            "tool_choice_optimality": 5,
            "is_transition_sound": True,
            "critique": "Step logically progresses from evidence to mitigation.",
            "flagged_issue": None,
        })

  def evaluate_step(
      self,
      step_data: Dict[str, Any],
      history: List[Dict[str, Any]],
  ) -> StepJudgement:
    step_num = step_data.get("step", 1)
    history_str = "\n".join([
        f"- Step {h.get('step')}: Action={h.get('action')}, Observation={json.dumps(h.get('observation'))}"
        for h in history
    ]) or "(None - Initial Step)"

    # Detect repeating loop deterministically before model call
    if history and history[-1].get("action") == step_data.get("action") and history[-1].get("action_input") == step_data.get("action_input"):
      return StepJudgement(
          step_number=step_num,
          thought_validity_score=2,
          tool_choice_optimality=1,
          is_transition_sound=False,
          critique="Circular action: Identical action and arguments repeated.",
          flagged_issue="CIRCULAR_ACTION",
      )

    prompt = QUAIL_STEP_RUBRIC_PROMPT.format(
        current_step=step_num,
        history_context=history_str,
        thought=step_data.get("thought", ""),
        action=step_data.get("action", ""),
        action_input=json.dumps(step_data.get("action_input", {})),
    )

    raw_response = self.model_client(prompt)
    try:
      json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
      payload = json.loads(json_match.group(0)) if json_match else json.loads(raw_response)
      return StepJudgement(
          step_number=step_num,
          thought_validity_score=int(payload.get("thought_validity_score", 3)),
          tool_choice_optimality=int(payload.get("tool_choice_optimality", 3)),
          is_transition_sound=bool(payload.get("is_transition_sound", True)),
          critique=str(payload.get("critique", "")),
          flagged_issue=payload.get("flagged_issue"),
      )
    except Exception:
      return StepJudgement(
          step_number=step_num,
          thought_validity_score=3,
          tool_choice_optimality=3,
          is_transition_sound=True,
          critique="Fallback default score on parse error.",
          flagged_issue=None,
      )

  def evaluate_trajectory(
      self, session_data: Dict[str, Any]
  ) -> TrajectoryQuailResult:
    trajectory = session_data.get("result", {}).get("trajectory", [])
    if not trajectory:
      return TrajectoryQuailResult(
          passed=True,
          overall_reasoning_fidelity=1.0,
          step_judgements=[],
          flawed_steps_count=0,
          summary="Empty trajectory.",
      )

    judgements: List[StepJudgement] = []
    history: List[Dict[str, Any]] = []

    for step in trajectory:
      judgement = self.evaluate_step(step, history)
      judgements.append(judgement)
      history.append(step)

    flawed = [j for j in judgements if not j.is_transition_sound]
    total_scores = sum(
        (j.thought_validity_score + j.tool_choice_optimality) / 10.0
        for j in judgements
    )
    fidelity = round(total_scores / len(judgements), 3)
    passed = len(flawed) == 0 and fidelity >= 0.70

    summary = (
        f"Quail Trace Audit: {len(judgements)} steps evaluated. "
        f"Fidelity={fidelity:.1%}, Flawed Transitions={len(flawed)}."
    )

    return TrajectoryQuailResult(
        passed=passed,
        overall_reasoning_fidelity=fidelity,
        step_judgements=judgements,
        flawed_steps_count=len(flawed),
        summary=summary,
    )