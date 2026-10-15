"""evals/schemas.py - Strongly typed schemas for synthetic scenario evaluation datasets."""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class PersonaArchetype(str, Enum):
  PANICKED_JUNIOR = "panicked_junior"
  TERSE_SRE = "terse_sre"
  NOISY_AUTOMATION = "noisy_automation"
  MISDIRECTED_DEV = "misdirected_dev"
  SOPHISTICATED_LEAD = "sophisticated_lead"


class IncidentSeverity(str, Enum):
  SEV1 = "SEV1"
  SEV2 = "SEV2"
  SEV3 = "SEV3"


class ExpectedToolCall(BaseModel):
  tool_name: str
  required_arguments: Dict[str, Any] = Field(default_factory=dict)
  optional_arguments: Dict[str, Any] = Field(default_factory=dict)
  order_index: int


class MockInfraSeed(BaseModel):
  logs: List[Dict[str, Any]] = Field(default_factory=list)
  db_tables: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
  metric_series: Dict[str, List[float]] = Field(default_factory=dict)


class IncidentScenario(BaseModel):
  scenario_id: str
  archetype: PersonaArchetype
  severity: IncidentSeverity
  target_service: str
  root_cause: str
  raw_user_prompt: str
  infra_seed: MockInfraSeed
  golden_trajectory: List[ExpectedToolCall]
  forbidden_actions: List[str] = Field(default_factory=list)
  expected_resolution_keywords: List[str] = Field(default_factory=list)


class ScenarioEvaluationBatch(BaseModel):
  batch_id: str
  generated_at: str
  scenarios: List[IncidentScenario]
