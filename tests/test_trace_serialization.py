"""tests/test_trace_serialization.py - Regression test suite for trace capture and replay."""

import os
import tempfile
import pytest
from tracer import GLOBAL_TRACER
from agent_core import ReActAgent, TOOL_REGISTRY
from trace_replay import SessionSerializer, DeterministicReplayAgent


@pytest.fixture(autouse=True)
def clean_global_tracer():
  GLOBAL_TRACER.clear()
  yield
  GLOBAL_TRACER.clear()


def test_agent_execution_creates_spans():
  agent = ReActAgent(tools=TOOL_REGISTRY)
  result = agent.run("Alert: High 500 error rate on checkout-api")

  assert result["status"] == "COMPLETED"
  assert result["steps_executed"] == 4

  spans = GLOBAL_TRACER.spans
  assert len(spans) > 0

  root_spans = [s for s in spans if s.span_kind == "AGENT"]
  assert len(root_spans) == 1
  assert root_spans[0].parent_id is None

  child_spans = [s for s in spans if s.parent_id == root_spans[0].span_id]
  assert len(child_spans) >= 4


def test_session_serialization_and_replay():
  agent = ReActAgent(tools=TOOL_REGISTRY)
  result = agent.run("Alert: Database pool exhausted")
  spans = GLOBAL_TRACER.spans

  with tempfile.TemporaryDirectory() as tmpdir:
    session_file = os.path.join(tmpdir, "session_replay.json")
    SessionSerializer.serialize_session(
        filepath=session_file,
        incident_id="INC-9901",
        agent_result=result,
        spans=spans,
    )

    session_data = SessionSerializer.load_session(session_file)
    replayer = DeterministicReplayAgent(session_data)
    replay_summary = replayer.replay_step_by_step()

    assert replay_summary["status"] == "REPLAY_SUCCESS"
    assert replay_summary["steps_replayed"] == len(result["trajectory"])
    assert replay_summary["final_resolution"] == result["resolution"]
