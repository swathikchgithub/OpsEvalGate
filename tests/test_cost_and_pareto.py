"""tests/test_sprint8.py - Unit tests for context compactor, KV cache profiler, and Pareto benchmark."""

import pytest
from evals.cost.context_compactor import ContextCompactor
from evals.cost.cache_profiler import PrefixCacheProfiler
from evals.cost.pareto_benchmark import ParetoCostAccuracyBenchmark


def test_context_compactor_deduplicates_repeated_logs():
    compactor = ContextCompactor(max_log_lines=3, max_field_length=50)

    # 10 logs with 8 identical messages
    noisy_logs = [
        {"timestamp": f"2026-10-12T00:0{i}Z", "service": "api", "level": "ERROR", "msg": "DB connection timeout"}
        for i in range(8)
    ]
    noisy_logs.append({"timestamp": "2026-10-12T00:09Z", "service": "api", "level": "FATAL", "msg": "Process killed"})

    res = compactor.compact_observation(noisy_logs)

    assert res.compression_ratio > 0.50
    assert res.truncated_items_count > 0
    assert len(res.compacted_payload) <= 3


def test_context_compactor_truncates_long_query_strings():
    compactor = ContextCompactor(max_field_length=40)
    long_msg = "SELECT * FROM orders WHERE customer_id = '12345' AND transaction_status = 'PENDING' AND retry_count > 5"
    res = compactor.compact_observation(long_msg)

    assert "[TRUNCATED]" in res.compacted_payload
    assert len(res.compacted_payload) <= 50
    assert res.compression_ratio > 0.0


def test_prefix_cache_profiler_computes_hit_rate_and_savings():
    profiler = PrefixCacheProfiler(
        static_system_instructions="You are OpsEvalGate SRE Agent with strict ReAct instructions.",
        static_tool_definitions=[{"name": "query_logs"}, {"name": "kill_db_process"}]
    )

    prompt_data = profiler.assemble_cached_prompt(
        incident_context="SEV1 Incident on checkout-api",
        trajectory_steps=[{"step": 1, "action": "query_logs"}],
        current_step_prompt="Determine if database connection pool is full."
    )

    # Turn 2: Should benefit from cached prefix + session history
    metrics_step2 = profiler.profile_run(step_number=2, prompt_structure=prompt_data)

    assert metrics_step2.cache_hit_rate >= 0.50
    assert metrics_step2.estimated_savings_usd > 0.0
    assert metrics_step2.cached_prefix_tokens > metrics_step2.dynamic_tokens


def test_pareto_benchmark_confirms_cost_reduction():
    benchmark = ParetoCostAccuracyBenchmark.generate_benchmark_report(sample_scenarios_count=50)

    assert benchmark["cost_reduction_vs_frontier"] >= 0.70  # >70% cheaper than monolithic frontier
    assert benchmark["latency_reduction_vs_frontier"] >= 0.50  # >50% faster
    assert "OpsEvalGate Adaptive" in benchmark["recommended_strategy"]