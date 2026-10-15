"""evals/attribution_reporter.py - Root-Cause Attribution & Markdown Diagnostic Reporter.

Isolates the exact pivot span where an agent trajectory derailed and generates
diagnostic markdown reports for post-mortems and PR regression analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from evals.failure_classifier import (
    ClassificationResult,
    FailureCategory,
    FailureModeClassifier,
)
from evals.trace_parser import SessionMetrics, TraceDiagnosticParser


@dataclass
class PivotSpanDetail:
  step_number: Optional[int]
  span_id: Optional[str]
  name: str
  span_kind: str
  latency_ms: float
  derailment_reason: str
  inputs: Any
  outputs: Any
  error_message: Optional[str] = None


class RootCauseAttributionReporter:
  """Analyzes trace telemetry to pinpoint pivot failure spans and format markdown post-mortems."""

  def __init__(self, registered_tools: Optional[List[str]] = None):
    self.classifier = FailureModeClassifier(registered_tools=registered_tools)
    self.parser = TraceDiagnosticParser()

  def identify_pivot_span(
      self,
      session_data: Dict[str, Any],
      classification: ClassificationResult,
  ) -> Optional[PivotSpanDetail]:
    """Isolates the specific span or trajectory step where execution derailed."""
    if classification.primary_failure == FailureCategory.SUCCESS:
      return None

    spans = session_data.get("spans", [])
    trajectory = session_data.get("result", {}).get("trajectory", [])
    offending_step = classification.offending_step

    # 1. Match from offending trajectory step
    if offending_step is not None and trajectory:
      target_step = next(
          (s for s in trajectory if s.get("step") == offending_step), None
      )
      if target_step:
        action_name = target_step.get("action", "")
        matched_span = next(
            (
                s
                for s in spans
                if action_name in s.get("name", "")
                or s.get("inputs") == target_step.get("action_input")
            ),
            None,
        )

        return PivotSpanDetail(
            step_number=offending_step,
            span_id=matched_span.get("span_id") if matched_span else None,
            name=matched_span.get("name", f"trajectory.step_{offending_step}"),
            span_kind=matched_span.get("span_kind", "TOOL"),
            latency_ms=matched_span.get("latency_ms", 0.0) if matched_span else 0.0,
            derailment_reason=classification.diagnostic_summary,
            inputs=target_step.get("action_input"),
            outputs=target_step.get("observation"),
            error_message=str(
                classification.raw_evidence.get("schema_error")
                or target_step.get("observation")
            ),
        )

    # 2. Match from recorded error spans
    error_span = next((s for s in spans if s.get("status") == "ERROR"), None)
    if error_span:
      return PivotSpanDetail(
          step_number=None,
          span_id=error_span.get("span_id"),
          name=error_span.get("name", "unknown_span"),
          span_kind=error_span.get("span_kind", "UNKNOWN"),
          latency_ms=float(error_span.get("latency_ms", 0.0) or 0.0),
          derailment_reason=f"Unhandled exception in span {error_span.get('name')}",
          inputs=error_span.get("inputs"),
          outputs=error_span.get("outputs"),
          error_message=error_span.get("status_message"),
      )

    # 3. Fallback to terminal step
    last_step = trajectory[-1] if trajectory else {}
    return PivotSpanDetail(
        step_number=last_step.get("step", len(trajectory)),
        span_id=None,
        name=last_step.get("action", "agent.react_loop"),
        span_kind="AGENT",
        latency_ms=0.0,
        derailment_reason=classification.diagnostic_summary,
        inputs=last_step.get("action_input"),
        outputs=last_step.get("observation"),
        error_message=classification.diagnostic_summary,
    )

  def recommend_remediation(self, failure: FailureCategory) -> str:
    """Returns tactical engineering guidance based on the failure taxonomy."""
    remediations = {
        FailureCategory.TOOL_HALLUCINATION: (
            "**Tool Registry Drift:** Enforce strict JSON Schema tool definitions"
            " and add system-prompt negative constraints against hallucinating"
            " unregistered functions."
        ),
        FailureCategory.SCHEMA_ARGUMENT_DRIFT: (
            "**Schema Mismatch:** Align the Pydantic tool parameter signature"
            " with the model's tool-call parser, or inject concrete argument"
            " few-shot examples into the agent prompt."
        ),
        FailureCategory.INFINITE_LOOP: (
            "**Observation Processing Defect:** Implement an in-memory trajectory"
            " cycle detector that short-circuits execution if the exact tool and"
            " parameters are called consecutively."
        ),
        FailureCategory.MAX_ITERATIONS_EXCEEDED: (
            "**Planning Inefficiency:** Provide progressive summarization in the"
            " scratchpad or break multi-phase triage into a hierarchical"
            " supervisor-worker pattern."
        ),
        FailureCategory.PREMATURE_TERMINATION: (
            "**Insufficient Verification:** Require explicit verification tool"
            " calls before allowing the model to emit a `final_answer`."
        ),
        FailureCategory.UNHANDLED_EXCEPTION: (
            "**Tool Boundary Fragility:** Wrap external API/DB calls with"
            " defensive error boundaries returning structured `{'status':"
            " 'FAILED', 'error': ...}` observations rather than bubbling raw"
            " exceptions."
        ),
        FailureCategory.SUCCESS: "No remediation required. Trajectory met all golden criteria.",
    }
    return remediations.get(
        failure, "Review execution traces manually to isolate edge-case errors."
    )

  def generate_markdown_report(
      self,
      session_data: Dict[str, Any],
      golden_tools: Optional[List[str]] = None,
  ) -> str:
    """Generates an executive diagnostic markdown report from a session trace."""
    self.parser._ingest_session(session_data)
    incident_id = session_data.get("incident_id", "UNKNOWN")
    metrics = self.parser.compute_session_metrics(incident_id)
    classification = self.classifier.classify(
        session_data, golden_tools=golden_tools
    )
    pivot = self.identify_pivot_span(session_data, classification)

    lines: List[str] = [
        f"# Incident Post-Mortem & Trace Attribution: `{incident_id}`",
        f"*Report Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*",
        "",
        "## Executive Summary",
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Final Status** | `{metrics.status}` |",
        f"| **Primary Failure** | `{classification.primary_failure.value}` |",
        f"| **Total Latency** | `{metrics.total_latency_ms} ms` |",
        f"| **Steps Executed** | `{metrics.total_steps}` |",
        f"| **Tool Invocations** | `{metrics.tool_call_count}` |",
        f"| **p50 / p95 Latency** | `{metrics.p50_span_latency_ms} ms` / `{metrics.p95_span_latency_ms} ms` |",
        "",
        "---",
        "",
        "## Diagnostic Taxonomy & Root Cause Attribution",
        "",
        f"- **Diagnostic Verdict:** {classification.diagnostic_summary}",
    ]

    if classification.contributing_factors:
      factors = ", ".join(
          f"`{f.value}`" for f in classification.contributing_factors
      )
      lines.append(f"- **Contributing Factors:** {factors}")

    if pivot:
      step_label = (
          f"Step {pivot.step_number}"
          if pivot.step_number
          else "Intermediate Span"
      )
      lines.extend([
          "",
          f"### Pivot Derailment Span: `{pivot.name}` ({step_label})",
          "",
          f"- **Span ID:** `{pivot.span_id or 'N/A'}`",
          f"- **Span Kind:** `{pivot.span_kind}`",
          f"- **Duration:** `{pivot.latency_ms} ms`",
          f"- **Failure Trigger:** {pivot.derailment_reason}",
          "",
          "**Input Arguments:**",
          "```json",
          json.dumps(pivot.inputs, indent=2) if pivot.inputs else "{}",
          "```",
          "",
          "**Observation / Error Output:**",
          "```text",
          str(pivot.outputs or pivot.error_message or "None"),
          "```",
      ])

    trajectory = session_data.get("result", {}).get("trajectory", [])
    if trajectory:
      lines.extend([
          "",
          "---",
          "",
          "## Step-by-Step Trajectory Audit",
          "",
          "| Step | Action Tool | Outcome / Status | Latency / Flag |",
          "| :---: | :--- | :--- | :--- |",
      ])

      for item in trajectory:
        st = item.get("step")
        action = item.get("action", "N/A")
        flag = (
            "⚠️ **PIVOT POINT**"
            if pivot and pivot.step_number == st
            else "✅ OK"
        )
        obs_preview = (
            str(item.get("observation", ""))[:60].replace("\n", " ") + "..."
        )
        lines.append(f"| {st} | `{action}` | {obs_preview} | {flag} |")

    remediation_text = self.recommend_remediation(classification.primary_failure)
    lines.extend([
        "",
        "---",
        "",
        "## Actionable Engineering Remediation",
        "",
        f"> {remediation_text}",
        "",
    ])

    return "\n".join(lines)