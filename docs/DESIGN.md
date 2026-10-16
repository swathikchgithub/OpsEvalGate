# Design Document

How the main components are designed, the security design, and where to extend. For structure and rationale see [ARCHITECTURE.md](ARCHITECTURE.md); for contracts and tests see [TDD.md](TDD.md).

## 1. Design principles

1. **Measure before you trust.** Every behavior change must be visible in metrics.
2. **Deterministic by default.** Seeds, mocks and rule-based planning; live models are opt-in.
3. **Fail closed.** Unknown tool, bad arguments, approver error or invalid decision means deny.
4. **Inject, don't instantiate.** Planner, tools, approver and evaluators are passed in.
5. **Keep answers out of the agent's view.** Golden trajectories and keywords live only in scenarios and evaluators.

## 2. Components

### 2.1 Scenario generator
Produces `IncidentScenario` objects from a catalog (checkout-api connection-pool exhaustion with a stuck PID; auth-service token blacklist) with seeded typo/noise injection on the user prompt. Same seed gives the same batch. Each scenario carries a golden trajectory and expected resolution keywords used only by evaluators.

### 2.2 Mock environment
`MockEnvironmentFactory.sandbox(scenario)` is a context manager yielding an environment with seeded logs and `pg_stat_activity` rows. `get_bound_tools()` returns tools bound to that instance, and `mutation_history` records destructive effects so tests can assert "nothing was killed".

### 2.3 ReAct agent
`ReActAgent(tools, model_client, max_iterations=5)`. Each iteration asks the planner for a thought and action, runs the tool, and appends the observation to the scratchpad. It ends with `COMPLETED` when the planner returns a final resolution, or `MAX_ITERATIONS_REACHED`. A denied or blocked tool call is just another observation, so the loop continues.

### 2.4 Planners
A planner is a callable `(prompt, iteration, scratchpad, **kwargs) -> {"thought", "action", "action_input"}`. `PlaybookPlanner` is the offline implementation: fuzzy-match the service from the prompt, query logs, query DB state, take the PID **from the observation**, request a kill, then resolve. `evals/clients.py` adapts Anthropic and OpenAI to the same shape.

### 2.5 Evaluators
- **Deterministic guards:** tool argument schemas, forbidden actions, required resolution keywords, SQL safety.
- **Trajectory metrics:** tool precision and recall versus the golden trajectory.
- **LLM judge:** groundedness and safety scores (1-5) with a JSON schema; the CI version is a deterministic stand-in.
- **Quail step judge:** per-step validity and transition soundness.
- **Calibrator:** Cohen's Kappa between judge and human ratings (default target 0.75).

A scenario passes only if deterministic, trajectory and judge checks all pass.

### 2.6 Regression gate
Pure function `RegressionGate.evaluate_deltas(candidate, baseline)` returns `(passed, violations, markdown)`. `run_ci_gate.py` loads the baseline (missing or malformed baseline exits with code 2, never silently passes) and exits non-zero on violation.

## 3. Security design

Assets: the (eventually real) infrastructure the tools touch, secrets, and the integrity of the evaluation.
Trust boundaries: **the model's output is untrusted**, and so is anything a tool returns (logs may be attacker-controlled).

| Threat | OWASP / LLM risk | Control | Status |
| :--- | :--- | :--- | :--- |
| Model calls a dangerous tool | A01, excessive agency | Deny-list stubs + fail-closed approval | Done (eval path) |
| Shell/SQL injection in arguments | A03 | Regex screening in guardrail, validated before approval | Done, heuristic (known bypasses) |
| Destructive action without a human | A01, A04 | Per-call approval on exact arguments, audit log | Done (eval runner, live demos via console approver, agent default deny-all) |
| Approver failure grants access | A05 | Exception, non-`Decision` or bad args all deny | Done |
| Indirect prompt injection via logs | LLM01 | Sanitize observations before scratchpad | **Open** |
| Runaway loops / cost | A04, LLM10 | `max_iterations` cap | Partial (no time/token cap) |
| Secret leakage | A02 | `.env` git-ignored, no secrets in CI, no keys in prompts | Done |
| Supply chain | A06, A08 | Pinned requirements; provider SDKs optional | Done (no `pip audit` job yet) |
| Tampering with the baseline | A08 | Baseline is a reviewed file; update needs explicit flag | Done |

Ordering matters: **guardrail, then approver, then tool.** A malicious call is rejected before a human is asked to approve it.

## 4. Error handling

- Unknown or blocked tool returns an observation (`BLOCKED` / `APPROVAL_DENIED`); it never raises into the loop.
- Missing baseline fails fast (exit 2) with an explanatory message.
- Evaluators validate judge output with pydantic and reject out-of-range scores.

## 5. Extension points

| To add | Do this |
| :--- | :--- |
| A tool | Add it to the environment's bound tools; **decide read-only vs destructive**. If read-only, add it to the gate's `read_only_tools`; otherwise do nothing, it is gated by default. Add a schema entry for deterministic checks |
| An incident type | Add a catalog entry with seed data, golden trajectory and keywords; add playbook handling; re-record the baseline and review the diff |
| An approver | Implement `__call__(tool_name, params) -> Decision` (e.g. Slack, ticketing). Keep it fail-closed |
| A planner or model | Provide a callable matching the planner shape; use `EvaluationRunner(planner_factory=...)` |
| An evaluator | Add under `evals/evaluators/`, call from `EvaluationRunner.run_scenario`, expose a pass flag |
| A gate rule | Add a check in `RegressionGate.evaluate_deltas` with a unit test for pass and fail sides |

## 6. Operational notes

- Re-record the baseline only through `--update-baseline`, then review `git diff` of `baseline_metrics.json` in the PR.
- If the gate fails on latency, check the 5 ms floor before changing thresholds.
- Treat `SandboxAutoApprover` as test-only; never import it from production code paths.
