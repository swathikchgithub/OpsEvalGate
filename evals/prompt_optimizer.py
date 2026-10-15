"""evals/prompt_optimizer.py - Dynamic Few-Shot Prompt Optimizer & Feedback Loop."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional
from evals.failure_classifier import FailureCategory


@dataclass
class FewShotExemplar:
  exemplar_id: str
  incident_type: str
  addressed_failure_mode: FailureCategory
  user_input: str
  golden_scratchpad: List[Dict[str, Any]]
  negative_rules: List[str] = field(default_factory=list)


DEFAULT_EXEMPLAR_LIBRARY: List[FewShotExemplar] = [
    FewShotExemplar(
        exemplar_id="EX-LOCK-01",
        incident_type="database_lock",
        addressed_failure_mode=FailureCategory.INFINITE_LOOP,
        user_input="Checkout API pool exhausted, timeout waiting for conn",
        golden_scratchpad=[
            {
                "thought": "Examine error logs for checkout-api to verify root cause.",
                "action": "query_logs",
                "action_input": {"service": "checkout-api", "level": "ERROR"},
            },
            {
                "thought": "Connection pool exhausted. Inspect pg_stat_activity for idle-in-transaction locks.",
                "action": "query_database_state",
                "action_input": {"table_name": "pg_stat_activity"},
            },
            {
                "thought": "Found PID 8492 stuck in 'idle in transaction'. Terminate connection 8492 immediately.",
                "action": "kill_db_process",
                "action_input": {"pid": 8492},
            },
        ],
        negative_rules=[
            "Do NOT query logs repeatedly if the same error is already retrieved.",
            "Do NOT drop or alter database tables under any circumstances.",
        ],
    ),
    FewShotExemplar(
        exemplar_id="EX-AUTH-02",
        incident_type="token_exhaustion",
        addressed_failure_mode=FailureCategory.SCHEMA_ARGUMENT_DRIFT,
        user_input="Auth worker queue depth > 10,000 / HTTP 504",
        golden_scratchpad=[
            {
                "thought": "Check error logs for auth-service to isolate queue backpressure.",
                "action": "query_logs",
                "action_input": {"service": "auth-service", "level": "ERROR"},
            },
            {
                "thought": "Worker starvation detected. Inspect token_blacklist table.",
                "action": "query_database_state",
                "action_input": {"table_name": "token_blacklist"},
            },
        ],
        negative_rules=[
            "Ensure tool arguments are strictly formatted as a JSON object matching required schema.",
        ],
    ),
]


class DynamicPromptOptimizer:
  """Dynamically optimizes system prompts by mining failure modes and selecting relevant exemplars."""

  BASE_SYSTEM_PROMPT = (
      "You are OpsEvalGate, an autonomous Site Reliability Engineering diagnostic assistant.\n"
      "Resolve customer-impacting operational incidents methodically using ReAct steps."
  )

  def __init__(self, exemplar_library: Optional[List[FewShotExemplar]] = None):
    self.library = exemplar_library or list(DEFAULT_EXEMPLAR_LIBRARY)

  def select_exemplars_for_incident(
      self,
      incident_prompt: str,
      recent_failure_category: Optional[FailureCategory] = None,
      max_exemplars: int = 2,
  ) -> List[FewShotExemplar]:
    """Selects the most contextually relevant few-shot exemplars."""
    matches: List[FewShotExemplar] = []

    # Priority 1: Match on recent failure category
    if recent_failure_category:
      for ex in self.library:
        if ex.addressed_failure_mode == recent_failure_category:
          matches.append(ex)

    # Priority 2: Keyword relevance matching
    prompt_lower = incident_prompt.lower()
    for ex in self.library:
      if ex not in matches:
        if any(w in prompt_lower for w in ["lock", "pool", "database", "pg_"]):
          if ex.incident_type == "database_lock":
            matches.append(ex)
        elif any(w in prompt_lower for w in ["token", "auth", "worker"]):
          if ex.incident_type == "token_exhaustion":
            matches.append(ex)

    return matches[:max_exemplars]

  def build_optimized_prompt(
      self,
      incident_prompt: str,
      recent_failure_category: Optional[FailureCategory] = None,
  ) -> str:
    """Builds a customized, hardened system prompt with dynamic exemplars and negative constraints."""
    exemplars = self.select_exemplars_for_incident(
        incident_prompt=incident_prompt,
        recent_failure_category=recent_failure_category,
    )

    prompt_sections = [self.BASE_SYSTEM_PROMPT]

    # Collect negative guardrail rules
    all_rules: List[str] = [
        "Never hallucinate tools outside the provided tool schema.",
        "Stop immediately and emit a final answer once remediation is complete.",
    ]
    for ex in exemplars:
      all_rules.extend(ex.negative_rules)

    prompt_sections.append("\n=== Operational Safety Constraints ===")
    for rule in sorted(list(set(all_rules))):
      prompt_sections.append(f"- {rule}")

    # Add Few-Shot Demonstrations
    if exemplars:
      prompt_sections.append("\n=== Reference Golden Trajectories ===")
      for i, ex in enumerate(exemplars, 1):
        prompt_sections.append(f"\n[Exemplar {i}: {ex.incident_type}]")
        prompt_sections.append(f"Incident: {ex.user_input}")
        for step in ex.golden_scratchpad:
          prompt_sections.append(
              f"Thought: {step['thought']}\n"
              f"Action: {step['action']}({json.dumps(step['action_input'])})"
          )

    return "\n".join(prompt_sections)