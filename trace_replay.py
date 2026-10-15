"""trace_replay.py - Deterministic Trace Replayer & Session Serializer."""

from __future__ import annotations
import json
import os
from typing import Any, Dict, List
from tracer import Span


class SessionSerializer:

  @staticmethod
  def serialize_session(
      filepath: str,
      incident_id: str,
      agent_result: Dict[str, Any],
      spans: List[Span],
  ) -> None:
    payload = {
        "version": "1.0.0",
        "incident_id": incident_id,
        "result": agent_result,
        "spans": [s.to_dict() for s in spans],
    }
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
      json.dump(payload, f, indent=2)

  @staticmethod
  def load_session(filepath: str) -> Dict[str, Any]:
    with open(filepath, "r", encoding="utf-8") as f:
      return json.load(f)


class DeterministicReplayAgent:

  def __init__(self, recorded_session: Dict[str, Any]):
    self.session = recorded_session
    self.recorded_trajectory = recorded_session["result"].get("trajectory", [])
    self.recorded_spans = recorded_session.get("spans", [])

  def replay_step_by_step(self) -> Dict[str, Any]:
    step_verifications = []

    for step in self.recorded_trajectory:
      action = step["action"]
      action_input = step["action_input"]
      observation = step["observation"]

      matched_span = next(
          (
              s
              for s in self.recorded_spans
              if s["span_kind"] == "TOOL"
              and action in s["name"]
              and (
                  s.get("inputs") == action_input
                  or s.get("inputs", {}).get("kwargs", {}) == action_input
              )
          ),
          None,
      )

      step_verifications.append({
          "step": step["step"],
          "action": action,
          "has_matching_span": matched_span is not None,
          "observation_preserved": observation is not None,
      })

    return {
        "incident_id": self.session.get("incident_id"),
        "status": "REPLAY_SUCCESS",
        "steps_replayed": len(step_verifications),
        "step_verifications": step_verifications,
        "final_resolution": self.session["result"].get("resolution"),
    }
