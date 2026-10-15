"""evals/cost/cache_profiler.py - Prompt prefix caching layout and KV-cache hit profiler."""

from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib
from typing import Any, Dict, List, Optional


@dataclass
class CacheMetrics:
    total_tokens: int
    cached_prefix_tokens: int
    dynamic_tokens: int
    cache_hit_rate: float
    estimated_cost_usd: float
    estimated_savings_usd: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PrefixCacheProfiler:
    """Structures prompt hierarchies to maximize provider KV-cache hits."""

    # Industry standard token pricing (per 1M tokens)
    PRICE_PER_1M_INPUT_FRESH = 3.00
    PRICE_PER_1M_INPUT_CACHED = 0.75  # ~75% discount on cache hits

    def __init__(self, static_system_instructions: str, static_tool_definitions: List[Dict[str, Any]]):
        self.static_system_instructions = static_system_instructions
        self.static_tool_definitions = static_tool_definitions
        self._cached_prefix_hash = self._compute_hash(
            self.static_system_instructions + json_str(self.static_tool_definitions)
        )

    @staticmethod
    def _compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rule of thumb: 1 token ≈ 4 characters."""
        return max(1, len(text) // 4)

    def assemble_cached_prompt(
        self,
        incident_context: str,
        trajectory_steps: List[Dict[str, Any]],
        current_step_prompt: str,
    ) -> Dict[str, Any]:
        """
        Organizes the prompt into Cacheable Block 1 (System + Tools),
        Cacheable Block 2 (Incident History), and Dynamic Block 3 (Current Turn).
        """
        static_block = (
            f"[STATIC_PREFIX: SYSTEM]\n{self.static_system_instructions}\n\n"
            f"[STATIC_PREFIX: TOOLS]\n{json_str(self.static_tool_definitions)}"
        )

        history_block = (
            f"[SESSION_CONTEXT]\n{incident_context}\n\n"
            f"[TRAJECTORY_HISTORY]\n{json_str(trajectory_steps)}"
        )

        dynamic_block = f"[CURRENT_TURN]\n{current_step_prompt}"

        full_prompt = f"{static_block}\n\n{history_block}\n\n{dynamic_block}"

        return {
            "full_prompt": full_prompt,
            "static_block": static_block,
            "history_block": history_block,
            "dynamic_block": dynamic_block,
            "static_prefix_hash": self._cached_prefix_hash,
        }

    def profile_run(
        self,
        step_number: int,
        prompt_structure: Dict[str, Any],
    ) -> CacheMetrics:
        """Calculates cache hit rate and token cost metrics for a step."""
        static_tokens = self._estimate_tokens(prompt_structure["static_block"])
        history_tokens = self._estimate_tokens(prompt_structure["history_block"])
        dynamic_tokens = self._estimate_tokens(prompt_structure["dynamic_block"])

        total_tokens = static_tokens + history_tokens + dynamic_tokens

        # On Step 1: Only static block is cached.
        # On Step 2+: Both static block and session history are eligible for cache hit.
        if step_number <= 1:
            cached_tokens = static_tokens
        else:
            cached_tokens = static_tokens + history_tokens

        fresh_tokens = total_tokens - cached_tokens
        cache_hit_rate = round(cached_tokens / total_tokens, 4) if total_tokens > 0 else 0.0

        # Cost without caching
        cost_unoptimized = (total_tokens / 1_000_000) * self.PRICE_PER_1M_INPUT_FRESH

        # Cost with caching
        cost_optimized = (
            (cached_tokens / 1_000_000) * self.PRICE_PER_1M_INPUT_CACHED
            + (fresh_tokens / 1_000_000) * self.PRICE_PER_1M_INPUT_FRESH
        )

        savings = max(0.0, cost_unoptimized - cost_optimized)

        return CacheMetrics(
            total_tokens=total_tokens,
            cached_prefix_tokens=cached_tokens,
            dynamic_tokens=fresh_tokens,
            cache_hit_rate=cache_hit_rate,
            estimated_cost_usd=round(cost_optimized, 6),
            estimated_savings_usd=round(savings, 6),
        )


def json_str(val: Any) -> str:
    import json
    return json.dumps(val, sort_keys=True)