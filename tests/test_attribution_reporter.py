"""tests/test_attribution_reporter.py - Test suite for root-cause attribution and markdown generation."""

import pytest
from evals.attribution_reporter import RootCauseAttributionReporter
from evals.failure_classifier import FailureCategory


def test_attribution_reporter_isolates_pivot_step():
  failing_session = {
      "incident_id": "INC-REVERT-404",
      "result": {
          "status": "COMPLETED",
          "trajectory": [
              {
                  "step": 1,
                  "action": "query_logs",
                  "action_input": {"service": "cart"},
                  "observation": [{"msg": "err"}],
              },
              {
                  "step": 2,
                  "action": "invalid_debug_shell",
                  "action_input": {"cmd": "ls"},
                  "observation": "Tool 'invalid_debug_shell' is not registered.",
              },
          ],
      },
      "spans": [
          {
              "span_id": "span-1",
              "name": "tool.query_logs",
              "span_kind": "TOOL",
              "parent_id": "root",
              "latency_ms": 14.2,
              "status": "OK",
          },
          {
              "span_id": "span-2",
              "name": "invalid_debug_shell",
              "span_kind": "TOOL",
              "parent_id": "root",
              "latency_ms": 1.1,
              "status": "ERROR",
              "status_message": "Unregistered tool",
          },
      ],
  }

  reporter = RootCauseAttributionReporter(
      registered_tools=["query_logs", "query_database_state"]
  )
  classification = reporter.classifier.classify(failing_session)
  pivot = reporter.identify_pivot_span(failing_session, classification)

  assert pivot is not None
  assert pivot.step_number == 2
  assert "invalid_debug_shell" in pivot.name
  assert classification.primary_failure == FailureCategory.TOOL_HALLUCINATION


def test_attribution_reporter_generates_markdown():
  session = {
      "incident_id": "INC-OK-101",
      "result": {
          "status": "COMPLETED",
          "resolution": "Fixed",
          "trajectory": [{
              "step": 1,
              "action": "query_logs",
              "action_input": {"service": "auth"},
              "observation": [{"msg": "cleared"}],
          }],
      },
      "spans": [{
          "span_id": "s-1",
          "name": "tool.query_logs",
          "span_kind": "TOOL",
          "parent_id": None,
          "latency_ms": 50.0,
          "status": "OK",
      }],
  }

  reporter = RootCauseAttributionReporter(registered_tools=["query_logs"])
  report_md = reporter.generate_markdown_report(session)

  assert "# Incident Post-Mortem & Trace Attribution: `INC-OK-101`" in report_md
  assert "| **Final Status** | `COMPLETED` |" in report_md
  assert "## Actionable Engineering Remediation" in report_md
  assert "SUCCESS" in report_md