"""tests/test_failure_classifier.py - Unit tests for failure taxonomy and trace parser."""

import pytest
from evals.failure_classifier import FailureCategory, FailureModeClassifier
from evals.trace_parser import TraceDiagnosticParser


def test_classify_tool_hallucination():
  mock_session = {
      "incident_id": "INC-ERR-01",
      "result": {
          "status": "COMPLETED",
          "trajectory": [{
              "step": 1,
              "action": "non_existent_reboot_command",
              "action_input": {"target": "all"},
              "observation": "Tool is not registered.",
          }],
      },
      "spans": [],
  }

  classifier = FailureModeClassifier(
      registered_tools=["query_logs", "query_database_state"]
  )
  result = classifier.classify(mock_session)
  assert result.primary_failure == FailureCategory.TOOL_HALLUCINATION
  assert result.offending_step == 1


def test_classify_infinite_loop():
  mock_session = {
      "incident_id": "INC-LOOP-02",
      "result": {
          "status": "MAX_ITERATIONS_REACHED",
          "trajectory": [
              {
                  "step": 1,
                  "action": "query_logs",
                  "action_input": {"service": "checkout"},
                  "observation": [],
              },
              {
                  "step": 2,
                  "action": "query_logs",
                  "action_input": {"service": "checkout"},
                  "observation": [],
              },
          ],
      },
      "spans": [],
  }

  classifier = FailureModeClassifier()
  result = classifier.classify(mock_session)
  assert result.primary_failure == FailureCategory.INFINITE_LOOP


def test_trace_diagnostic_parser_metrics():
  parser = TraceDiagnosticParser()
  sample_session = {
      "incident_id": "INC-PERF-03",
      "result": {"status": "COMPLETED", "trajectory": [{"step": 1}]},
      "spans": [
          {
              "span_id": "s1",
              "name": "agent.react_loop",
              "span_kind": "AGENT",
              "parent_id": None,
              "latency_ms": 150.0,
              "status": "OK",
          },
          {
              "span_id": "s2",
              "name": "tool.query_logs",
              "span_kind": "TOOL",
              "parent_id": "s1",
              "latency_ms": 120.0,
              "status": "OK",
          },
      ],
  }

  parser._ingest_session(sample_session)
  metrics = parser.compute_session_metrics("INC-PERF-03")
  assert metrics.incident_id == "INC-PERF-03"
  assert metrics.total_latency_ms == 150.0
  assert metrics.tool_call_count == 1
