# Decision Log

Architecture decision records (ADRs): why OpsEvalGate is built the way it is. A summary lives in [ARCHITECTURE.md](ARCHITECTURE.md); this file is the source of truth.

**Status values:** Accepted, Accepted with known gap, Superseded (by ADR-n).
**Rule:** do not edit an accepted decision to change its meaning. Add a new ADR that supersedes it.

## Index

| ADR | Decision | Status |
| :--- | :--- | :--- |
| 1 | Evaluation harness + CI gate as the core product | Accepted |
| 2 | Deterministic, offline-first evaluation | Accepted |
| 3 | Rule-based playbook planner, blind to golden answers | Accepted |
| 4 | Small explicit ReAct loop with an iteration cap | Accepted |
| 5 | Defense in depth for tool use | Accepted with known gap |
| 6 | Fail-closed approval via a read-only allowlist | Accepted with known gap |
| 7 | Gate on deltas vs a stored baseline; zero-tolerance safety | Accepted |
| 8 | Span tracing as the audit trail | Accepted with known gap |
| 9 | Plain Python, injected dependencies, small dependency set | Accepted |
| 10 | One CI workflow, no soft-fail | Accepted |
| 11 | Baseline is a committed, reviewed file | Accepted |
| 12 | Synchronous approvers, no timeout | Accepted with known gap |
| 13 | Project name: OpsEvalGate | Accepted |
| 14 | Test-first development for every behavior | Accepted |

---

## ADR-1: Evaluation harness plus a CI gate as the core product
- **Context:** agent quality shifts with every prompt, tool or model edit, and humans cannot re-verify by hand.
- **Decision:** treat evaluation as the product. A stored baseline and an automatic gate make regression a build failure.
- **Consequences:** scenarios and a baseline must be maintained; the baseline is re-recorded deliberately when behavior legitimately changes.
- **Alternatives:** manual prompt QA (not repeatable or enforceable on PRs); "just improve the prompt" (no way to know it helped).

## ADR-2: Deterministic, offline-first evaluation
- **Context:** live LLM calls are slow, costly, nondeterministic and need secrets in CI.
- **Decision:** seeded scenarios, an in-memory mock environment and a rule-based offline planner. The planner interface also accepts live clients.
- **Consequences:** the CI baseline tests the pipeline and rules, not a model. Live-model runs are a separate, opt-in step.
- **Alternatives:** live-LLM evaluation in CI (cost, flakiness, keys in CI).

## ADR-3: Rule-based playbook planner, blind to golden answers
- **Context:** an earlier hardcoded planner always produced the right answer, so a 100% baseline proved nothing.
- **Decision:** `PlaybookPlanner` sees only the prompt and tool observations. A test fails if its source references golden fields (`golden_trajectory`, `expected_resolution_keywords`, `scenario_generator`).
- **Consequences:** noisy inputs expose real failures (3 of 800 corrupted scenarios fail), so the metric can move. It is still a stand-in for an LLM and does not model hallucination.
- **Alternatives:** replay golden trajectories (tautological); call a live model in CI (see ADR-2).

## ADR-4: Small explicit ReAct loop with an iteration cap
- **Context:** triage needs observe-then-act cycles; unbounded loops are a cost and safety risk.
- **Decision:** a short, readable loop (`max_iterations=5`) with a visible scratchpad, instead of an agent framework.
- **Consequences:** every step is traceable and testable. No built-in memory or parallel tools. A wall-clock/token budget is still open work.
- **Alternatives:** LangChain/LangGraph (hides the loop we must trace; large dependency surface).

## ADR-5: Defense in depth for tool use
- **Context:** regex guardrails alone are bypassable, and an LLM-chosen tool call is untrusted input.
- **Decision:** independent layers: tool deny-list, argument screening, fail-closed approval, per-scenario sandbox, tracing and audit. Order is guardrail, then approver, then tool, so a malicious call never reaches a human prompt.
- **Consequences:** more moving parts; each layer has its own tests.
- **Known gap:** regex patterns have false positives (`--`, `;`) and bypasses (newlines, `$IFS`, URL-encoding); tool output is not yet sanitized (indirect prompt injection).

## ADR-6: Fail-closed approval via a read-only allowlist
- **Context:** a denylist of "dangerous" tools silently permits any new tool.
- **Decision:** only `query_logs` and `query_database_state` skip approval. Everything else needs `Decision(True)` per call on the exact arguments. Approver exceptions, non-`Decision` returns and bad arguments all deny. The default approver is deny-all. Eval runs use `SandboxAutoApprover`.
- **Consequences:** new read-only tools must be listed explicitly. New destructive tools are safe by default.
- **Known gap:** nothing structurally prevents wiring `SandboxAutoApprover` into a live agent (a test only checks `agent_core` does not reference it). The live demos use `ConsoleApprover` and `ReActAgent()` defaults to a deny-all gate, but a caller passing `tools=` explicitly must gate them itself.
- **Alternatives:** denylist of destructive tools (fails open); approval once per session (approving PID A must not approve PID B).

## ADR-7: Gate on deltas against a stored baseline, zero-tolerance safety
- **Context:** absolute thresholds are arbitrary; relative thresholds track the accepted state.
- **Decision:** safety pass rate must be 100%; success may drop at most 2 points; tool recall at most 5; latency may rise 30% only if it also exceeds a 5 ms noise floor.
- **Consequences:** thresholds are policy and need tuning. Slow drift under the limits can accumulate until the baseline is re-recorded.
- **Alternatives:** absolute pass marks only (cannot say "no worse than before").

## ADR-8: Span tracing as the audit trail
- **Context:** debugging or attributing a failure needs every step's inputs and outputs.
- **Decision:** OpenInference-style spans (AGENT, CHAIN, LLM, TOOL) collected in memory and serialized per session for replay and diagnostics.
- **Consequences:** failures can be classified and attributed to a pivot step.
- **Known gap:** `GLOBAL_TRACER` is process-global, so runs are single-threaded.

## ADR-9: Plain Python, injected dependencies, small dependency set
- **Context:** this is a reference implementation and a CI tool; supply-chain surface matters.
- **Decision:** core needs only pydantic, pytest and python-dotenv, all pinned. Provider SDKs are optional (`requirements-llm.txt`). Planners, approvers and tool maps are injected.
- **Consequences:** each part is swappable in tests. Less out-of-the-box functionality than a framework.

## ADR-10: One CI workflow, no soft-fail
- **Context:** two overlapping workflows ran on every PR, and the gate step used `continue-on-error` and failed later, which obscured results.
- **Decision:** a single workflow (`eval_gate.yml`) on pull requests, pushes to `main` and manual dispatch, pinned to `ubuntu-24.04` and Python 3.9. The gate step fails the job directly.
- **Consequences:** one source of truth for required checks. No secrets are needed.

## ADR-11: Baseline is a committed, reviewed file
- **Context:** a hardcoded fake baseline made the gate meaningless.
- **Decision:** `evals/baselines/baseline_metrics.json` is generated by `python scripts/run_ci_gate.py --update-baseline` and committed. A missing or malformed baseline exits with code 2 and never passes silently.
- **Consequences:** changing the baseline is visible in a PR diff. `evals/.gitignore` has an explicit negation so the file is not ignored.

## ADR-12: Synchronous approvers, no timeout
- **Context:** `ConsoleApprover` uses blocking `input()`, which cannot be time-limited cleanly in synchronous code.
- **Decision:** approvers are synchronous callables. Errors and EOF deny; there is no timeout.
- **Consequences:** a human who walks away leaves the agent waiting. Revisit if approvers become asynchronous (chat, ticketing).

## ADR-13: Project name is OpsEvalGate
- **Context:** the original name was "OpsCopilot". "OpsEval" is an existing IT-operations LLM benchmark (FSE 2025), and the nearest neighbor for the chosen name is `evalgate-ci` on PyPI.
- **Decision:** rename to OpsEvalGate and state in the README that it is unrelated to those projects.
- **Consequences:** PyPI availability of `opsevalgate` is unverified. `make_zip.py` still contains old-name strings and is slated for removal.

## ADR-14: Test-first development for every behavior
- **Context:** this tool exists to catch regressions, so its own controls must be provably enforced.
- **Decision:** write a failing test, then the code, then refactor. Every security control has a test that fails if the control is removed. See [TEST_DRIVEN_DEVELOPMENT.md](TEST_DRIVEN_DEVELOPMENT.md).
- **Consequences:** slightly slower first commit, much cheaper refactors. Older tests do not all follow the one-assertion convention yet.

---

## Template for a new ADR

```markdown
## ADR-N: Title
- **Context:** the forces and constraints at play.
- **Decision:** what we chose, in one or two sentences.
- **Consequences:** what gets easier, what gets harder.
- **Known gap / Alternatives:** optional.
```
