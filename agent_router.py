"""
agent_router.py - Adaptive model routing engine for cost and latency optimization.

Dispatches incidents across model tiers (Lightweight vs. Frontier Reasoning)
based on ambiguity, system severity, and in-flight failure detection.
"""

from __future__ import annotations
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from tracer import trace_span


class ModelTier(str, Enum):
    LIGHTWEIGHT = "LIGHTWEIGHT"  # Fast, low-cost (e.g., mini / flash tier)
    FRONTIER_REASONING = "FRONTIER_REASONING"  # Deep reasoning (e.g., o-series / sonnet tier)


@dataclass
class RoutingDecision:
    selected_tier: ModelTier
    model_id: str
    complexity_score: float  # 0.0 (simplest) to 1.0 (most complex)
    routing_reason: str
    detected_indicators: List[str]
    escalation_recommended: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IncidentModelRouter:
    """Heuristic and policy-driven router dispatching incident queries to optimal model tiers."""

    COMPLEXITY_TRIGGERS = [
        (r"\b(deadlock|lock|blocking|idle in transaction)\b", "Database lock contention", 0.40),
        (r"\b(oom|memory leak|heap|starvation)\b", "Resource leak / memory exhaustion", 0.35),
        (r"\b(cascading|upstream|cross-service|intermittent)\b", "Distributed system ambiguity", 0.35),
        (r"\b(sev-?1|critical outage|p0)\b", "High severity incident tier", 0.30),
        (r"\b(why|root cause|diagnose|investigate)\b", "Open-ended causal inquiry", 0.20),
    ]

    SIMPLE_TRIGGERS = [
        (r"^(fetch|show|get|display|view)\s+logs\b", "Direct log fetch", -0.30),
        (r"^(status|health|ping|check)\b", "Trivial health check query", -0.30),
        (r"\b(restart container|bounce service)\b", "Pre-determined routine action", -0.20),
    ]

    def __init__(
        self,
        lightweight_model_id: str = "fast-mini-eval",
        frontier_model_id: str = "frontier-reasoning-eval",
        complexity_threshold: float = 0.45,
    ):
        self.lightweight_model_id = lightweight_model_id
        self.frontier_model_id = frontier_model_id
        self.complexity_threshold = complexity_threshold

    @trace_span(name="router.classify_complexity", span_kind="CHAIN")
    def calculate_complexity(self, prompt: str, severity: Optional[str] = None) -> Tuple[float, List[str]]:
        """Calculates a normalized complexity score and extracts matching indicators."""
        score = 0.20  # Base prior score
        indicators: List[str] = []
        text = prompt.lower()

        if severity and severity.upper() in ("SEV1", "P0", "CRITICAL"):
            score += 0.30
            indicators.append(f"Severity: {severity}")

        for pattern, label, weight in self.COMPLEXITY_TRIGGERS:
            if re.search(pattern, text):
                score += weight
                indicators.append(label)

        for pattern, label, weight in self.SIMPLE_TRIGGERS:
            if re.search(pattern, text):
                score += weight
                indicators.append(label)

        sentences = [s for s in prompt.split(".") if len(s.strip()) > 5]
        if len(sentences) > 3:
            score += 0.15
            indicators.append("Multi-sentence contextual ambiguity")

        normalized_score = round(max(0.0, min(1.0, score)), 3)
        return normalized_score, indicators

    @trace_span(name="router.select_tier", span_kind="CHAIN")
    def route_incident(
        self,
        incident_prompt: str,
        severity: Optional[str] = None,
    ) -> RoutingDecision:
        """Selects the model tier based on prompt complexity analysis."""
        complexity, indicators = self.calculate_complexity(incident_prompt, severity)

        if complexity >= self.complexity_threshold:
            tier = ModelTier.FRONTIER_REASONING
            model = self.frontier_model_id
            reason = (
                f"Complexity score ({complexity:.2f} >= {self.complexity_threshold}) "
                f"requires frontier reasoning. Matched: {', '.join(indicators) or 'None'}"
            )
        else:
            tier = ModelTier.LIGHTWEIGHT
            model = self.lightweight_model_id
            reason = (
                f"Complexity score ({complexity:.2f} < {self.complexity_threshold}) "
                f"safe for lightweight model. Matched: {', '.join(indicators) or 'None'}"
            )

        return RoutingDecision(
            selected_tier=tier,
            model_id=model,
            complexity_score=complexity,
            routing_reason=reason,
            detected_indicators=indicators,
        )

    def should_escalate_in_flight(
        self,
        current_step: int,
        trajectory: List[Dict[str, Any]],
        has_error: bool = False,
    ) -> bool:
        """Dynamic circuit-breaker: flags whether an ongoing lightweight run should escalate."""
        if has_error:
            return True

        if current_step >= 2 and len(trajectory) >= 2:
            last_action = trajectory[-1].get("action")
            prev_action = trajectory[-2].get("action")
            if last_action == prev_action:
                return True

        if current_step >= 4:
            return True

        return False