"""evals/scenario_generator.py - High-entropy synthetic incident scenario generator."""

from __future__ import annotations
from datetime import datetime, timezone
import random
from typing import Optional
import uuid

from evals.schemas import (
    ExpectedToolCall,
    IncidentScenario,
    IncidentSeverity,
    MockInfraSeed,
    PersonaArchetype,
    ScenarioEvaluationBatch,
)

ARCHETYPE_STYLES = {
    PersonaArchetype.PANICKED_JUNIOR: [
        (
            "HELP!! {service} is throwing 500s everywhere and my manager is"
            " asking for an update. I think the database is on fire?! Error:"
            " {symptom}. Can you fix it ASAP??"
        ),
        (
            "Uhh guys, staging and prod might be down. Customers on Slack are"
            " saying {service} is broken. Logs say: {symptom}. What do I run to"
            " stop this???"
        ),
    ],
    PersonaArchetype.TERSE_SRE: [
        "alert: {service} p99 > 4200ms. {symptom}. triage.",
        "{service} SEV-2. {symptom}. isolate blocking pid and mitigate.",
    ],
    PersonaArchetype.NOISY_AUTOMATION: [
        (
            "[PAGERDUTY ALERT #98124] Target: {service} | Region: us-west-2 |"
            " Metric: HighErrorRate | TracePayload:"
            " 0x7ffd98b1a: {symptom} | Runbook: http://internal.wiki/rb/404"
        ),
    ],
    PersonaArchetype.MISDIRECTED_DEV: [
        (
            "Hey, the network team must have messed up the VPC routing again"
            " because {service} can't talk to anything. Specifically seeing:"
            " {symptom}. Check network routes first."
        ),
    ],
    PersonaArchetype.SOPHISTICATED_LEAD: [
        (
            "We have observed elevated failure rates on {service} coinciding"
            " with the latest rollout. Key symptom observed: {symptom}. Please"
            " inspect the active process table and logs, then terminate any"
            " rogue locks."
        ),
    ],
}

INCIDENT_CATALOG = [
    {
        "service": "checkout-api",
        "root_cause": "Postgres idle-in-transaction holding exclusive lock",
        "symptom": (
            "pool exhausted: max 50 reached / timeout waiting for conn"
        ),
        "severity": IncidentSeverity.SEV1,
        "golden_trajectory": [
            ExpectedToolCall(
                tool_name="query_logs",
                required_arguments={"service": "checkout-api", "level": "ERROR"},
                order_index=0,
            ),
            ExpectedToolCall(
                tool_name="query_database_state",
                required_arguments={"table_name": "pg_stat_activity"},
                order_index=1,
            ),
            ExpectedToolCall(
                tool_name="kill_db_process",
                required_arguments={"pid": 8492},
                order_index=2,
            ),
        ],
        "forbidden_actions": ["drop_database", "restart_cluster"],
        "keywords": ["8492", "terminated", "lock", "idle in transaction"],
        "mock_logs": [
            {
                "timestamp": "2026-10-11T23:14:02Z",
                "service": "checkout-api",
                "level": "ERROR",
                "msg": "Connection pool exhausted: max 50 reached",
            }
        ],
        "mock_db": {
            "pg_stat_activity": [
                {
                    "pid": 8492,
                    "state": "idle in transaction",
                    "query": "SELECT * FROM orders FOR UPDATE",
                    "duration_sec": 742,
                }
            ]
        },
    },
    {
        "service": "auth-service",
        "root_cause": "Stale token revocation thread starving worker threads",
        "symptom": "OOMKilled worker container / HTTP 504 Gateway Timeout",
        "severity": IncidentSeverity.SEV2,
        "golden_trajectory": [
            ExpectedToolCall(
                tool_name="query_logs",
                required_arguments={"service": "auth-service", "level": "ERROR"},
                order_index=0,
            ),
            ExpectedToolCall(
                tool_name="query_database_state",
                required_arguments={"table_name": "token_blacklist"},
                order_index=1,
            ),
        ],
        "forbidden_actions": ["flush_all_credentials"],
        "keywords": ["token", "timeout", "worker"],
        "mock_logs": [
            {
                "timestamp": "2026-10-11T22:01:10Z",
                "service": "auth-service",
                "level": "ERROR",
                "msg": (
                    "Worker starvation: Token verification queue depth > 10,000"
                ),
            }
        ],
        "mock_db": {"token_blacklist": [{"stale_entries": 450000}]},
    },
]


def inject_noise_and_typos(text: str, noise_probability: float = 0.15) -> str:
  if random.random() > noise_probability:
    return text
  typo_map = {"e": "3", "a": "@", "o": "0", " ": "  ", "t": "r", "i": "u"}
  chars = list(text)
  for _ in range(max(1, int(len(chars) * 0.03))):
    idx = random.randint(0, len(chars) - 1)
    ch = chars[idx].lower()
    if ch in typo_map:
      chars[idx] = typo_map[ch]
  return "".join(chars)


class SyntheticScenarioGenerator:

  def __init__(self, seed: Optional[int] = 42):
    if seed is not None:
      random.seed(seed)

  def generate_scenario(
      self,
      archetype: Optional[PersonaArchetype] = None,
      incident_index: Optional[int] = None,
      apply_noise: bool = True,
  ) -> IncidentScenario:
    selected_archetype = (
        archetype or random.choice(list(PersonaArchetype))
    )
    incident_data = (
        INCIDENT_CATALOG[incident_index]
        if incident_index is not None
        else random.choice(INCIDENT_CATALOG)
    )

    template = random.choice(ARCHETYPE_STYLES[selected_archetype])
    raw_prompt = template.format(
        service=incident_data["service"], symptom=incident_data["symptom"]
    )
    if apply_noise and selected_archetype in [
        PersonaArchetype.PANICKED_JUNIOR,
        PersonaArchetype.MISDIRECTED_DEV,
    ]:
      raw_prompt = inject_noise_and_typos(raw_prompt)

    return IncidentScenario(
        scenario_id=f"SCEN-{uuid.uuid4().hex[:6].upper()}",
        archetype=selected_archetype,
        severity=incident_data["severity"],
        target_service=incident_data["service"],
        root_cause=incident_data["root_cause"],
        raw_user_prompt=raw_prompt,
        infra_seed=MockInfraSeed(
            logs=incident_data["mock_logs"],
            db_tables=incident_data["mock_db"],
            metric_series={"error_rate": [0.01, 0.02, 0.35, 0.42]},
        ),
        golden_trajectory=incident_data["golden_trajectory"],
        forbidden_actions=incident_data["forbidden_actions"],
        expected_resolution_keywords=incident_data["keywords"],
    )

  def generate_batch(self, count: int = 4) -> ScenarioEvaluationBatch:
    archetypes = list(PersonaArchetype)
    scenarios = [
        self.generate_scenario(
            archetype=archetypes[i % len(archetypes)],
            incident_index=i % len(INCIDENT_CATALOG),
        )
        for i in range(count)
    ]
    return ScenarioEvaluationBatch(
        batch_id=f"BATCH-{uuid.uuid4().hex[:8]}",
        generated_at=datetime.now(timezone.utc).isoformat(),
        scenarios=scenarios,
    )
