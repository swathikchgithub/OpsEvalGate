"""evals/failure_classifier.py - Automated Failure Taxonomy Classifier for Agent Traces."""

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
