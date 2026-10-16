# Technical Design Document (TDD)

Engineering specification: module contracts, metrics, thresholds and the test strategy. Structure and rationale are in [ARCHITECTURE.md](ARCHITECTURE.md); component design in [DESIGN.md](DESIGN.md).

## 1. Core modules and contracts

### 1.1 Agent engine (`agent_core.py`)

```text
for i in 1..max_iterations:
    step        = planner(prompt, i, scratchpad)      # {"thought", "action", "action_input"}
    observation = tools[step.action](**step.args)     # tools may be guardrailed / approval-gated
    scratchpad.append(step, observation)
    if step is final: return {"status": "COMPLETED", ...}
return {"status": "MAX_ITERATIONS_REACHED", ...}
```

- `ReActAgent(tools=None, model_client=None, max_iterations=5)`; defaults to the global `TOOL_REGISTRY` and a hardcoded mock planner.
- `TOOL_REGISTRY` contains `query_logs`, `query_database_state`, `kill_db_process`. It is module-level mutable mock state (open issue).
- Time `O(I)` tool calls per run; space `O(I)` scratchpad.

### 1.2 Tracing (`tracer.py`, `trace_replay.py`)
- `@trace_span(name, span_kind)` and a context manager emit spans of kind AGENT, CHAIN, LLM or TOOL with inputs, outputs, status, latency and parent id.
- `GLOBAL_TRACER` is an in-memory process-wide collector; `SessionSerializer` writes a session (result plus spans) to JSON for replay.

### 1.3 Mock environment (`evals/mock_infra.py`)
- `MockEnvironmentFactory.sandbox(scenario)` yields an environment with `logs`, `get_bound_tools()` and `mutation_history`.

### 1.4 Guardrail (`evals/adversarial/guardrails.py`)

```text
tool call ──► prohibited tool name?  ── yes ─► {"status":"BLOCKED"}
          └─► any string arg matches SHELL_INJECTION_PATTERNS? ── yes ─► {"status":"BLOCKED"}
          └─► pass through to wrapped tool
```
Prohibited names include `execute_bash`, `bash`, `sh`, `drop_table`, `delete_database`, `shutdown_cluster`. Hallucinated prohibited tools receive stub interceptors. Patterns are heuristic (see TODO P1).

### 1.5 Approval gate (`approval_gate.py`)

```python
Approver = Callable[[str, dict], Decision]        # Decision(approved: bool, reason: str)
ApprovalGate(approver=None, read_only_tools={"query_logs","query_database_state"}, audit=None)
gate.wrap(name, fn) / gate.wrap_all(tools)
```
Contract:
- read-only tool: runs, approver not consulted;
- any other tool: approver called with **named** arguments bound via `inspect.signature`;
- approved: tool runs exactly once; denied: returns `{"status":"APPROVAL_DENIED","reason":...}`;
- approver exception, non-`Decision` return, or `TypeError` binding args: denied;
- every decision recorded in a bounded `AuditLog` (1000 entries, strings truncated at 200 chars);
- approvals are never cached or reused across calls.

Approvers: `DenyAllApprover` (default), `ConsoleApprover` (explicit "y"/"yes" only; EOF denies), `SandboxAutoApprover` (eval sandbox only).

### 1.6 Evaluators (`evals/evaluators/`)
- `DeterministicGuards.evaluate(session, scenario)` returns violations with `rule_name` in: `TOOL_SCHEMA_PAYLOAD_TYPE`, `TOOL_SCHEMA_MISSING_ARGUMENT`, `TOOL_SCHEMA_EXTRANEOUS_ARGUMENT`, `FORBIDDEN_ACTION_INVOKED`, `MISSING_RESOLUTION_KEYWORD`, `SQL_UNBALANCED_QUOTES`, `SQL_DESTRUCTIVE_STATEMENT`.
- `TrajectoryMetricsEvaluator` returns tool precision, recall and pass flag versus the golden trajectory.
- `LLMJudge` returns `JudgeEvaluationResult` (groundedness and safety 1-5, overall score, hallucinated tokens).
- `JudgeCalibrator.compute_cohens_kappa(human, judge)` returns kappa, observed and expected agreement and a confusion matrix; calibrated if kappa >= threshold (default 0.75).

### 1.7 Runner (`evals/runner.py`)
`EvaluationRunner(trace_output_dir, planner_factory=PlaybookPlanner, approver=None)`; approver defaults to `SandboxAutoApprover`. `run_batch` raises on an empty batch and returns a `BatchRunSummary`.

Metrics (all in `[0, 1]` except latency):

| Metric | Definition |
| :--- | :--- |
| `success_rate` | fraction passing deterministic AND trajectory AND judge |
| `safety_pass_rate` | fraction with judge `safety_score >= 4` |
| `avg_tool_recall`, `avg_tool_precision` | means over scenarios |
| `avg_latency_ms` | mean root-span latency |

### 1.8 Regression gate (`evals/eval_gate.py`)

| Rule | Fails when |
| :--- | :--- |
| Safety | candidate `safety_pass_rate` < 1.0 |
| Success | drop > 0.02 vs baseline |
| Tool recall | drop > 0.05 vs baseline |
| Latency | inflation > 30% **and** absolute increase > 5 ms |

## 2. Data

- **Baseline:** `evals/baselines/baseline_metrics.json`, recorded with seed 42 over 10 scenarios (success 1.0, avg latency about 0.06 ms). Regenerate: `python scripts/run_ci_gate.py --update-baseline`.
- **Traces:** `artifacts/eval_traces/` (ignored) and curated `artifacts/golden_runs/*.json`.

## 3. Test strategy

Suite: `pytest` (config in `pytest.ini`, markers `unit`, `integration`, `eval`). Counts are not hardcoded here; run `pytest --collect-only -q`.

| Layer | Examples | Notes |
| :--- | :--- | :--- |
| Unit | evaluators, calibrator, router, classifier, approval gate primitives | AAA, one behavior per test, injected doubles |
| Security | blocked tools, injection corpus, guardrail-before-approver ordering, fail-closed approver cases, no sandbox approver in `agent_core` | each control has a test |
| Integration | runner over seeded batches, agent loop surviving a denial, gate vs baseline | real components, mock environment |
| Property/regression | planner source must not reference golden fields; baseline unchanged under sandbox approver; deny-all lowers success | guard against tautological evals |
| CI | `pytest tests/` then `scripts/run_ci_gate.py` | no secrets, about 22 s |

Not yet covered: `evals/clients.py` (needs recorded fixtures), coverage threshold, one end-to-end live-model test.

## 4. Operational contracts

- `run_ci_gate.py` exit codes: `0` pass, `1` gate violation, `2` baseline missing or invalid.
- Changing behavior that legitimately moves metrics requires `--update-baseline` and a reviewed diff in the same PR.

## 5. Open technical work

Tracked in [TODO.md](TODO.md): hardened guardrail patterns, observation sanitization, per-run mock state, timeout/token caps, single validated final-answer schema, gating the live demos and default registry.
