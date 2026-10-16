# Product Requirements Document (PRD)

**Product:** OpsEvalGate  **Status:** Living document  **Owner:** repo maintainer

## 1. Problem

Teams building LLM agents for incident triage change prompts, tools, guardrails and models constantly. Each change can silently make the agent less accurate, less safe or slower, and manual spot-checking does not catch it. Agents that can act (kill a DB process, restart a service) raise the stakes: a regression can be an outage.

## 2. Vision

> No change to an incident-triage agent merges unless an automated, reproducible evaluation shows it is no worse than the last accepted version, and never less safe.

## 3. Users

| User | Need |
| :--- | :--- |
| **Agent developer** | Fast, deterministic feedback on whether a prompt/tool/guardrail change regressed behavior |
| **Reviewer / tech lead** | A pass/fail signal and a metric table on the PR, without re-running anything |
| **Security / SRE owner** | Evidence that destructive actions are gated and injection attempts are blocked |
| **Researcher / learner** | A small, readable reference of agent + eval + gate patterns |

## 4. Goals and non-goals

**Goals**
- G1. Run an agent against reproducible incident scenarios and score it automatically.
- G2. Block merges on quality, safety or latency regression against a stored baseline.
- G3. Keep destructive tools behind explicit, auditable approval (fail closed).
- G4. Capture full traces so any failure can be replayed and attributed to a step.
- G5. Run in CI with no secrets, no network, in well under a minute.

**Non-goals**
- Managing real infrastructure or replacing on-call engineers.
- Ranking models on SRE skill (the offline baseline is not a model benchmark).
- A UI, multi-tenant service or hosted product.

## 5. Functional requirements

| ID | Requirement | Where |
| :--- | :--- | :--- |
| FR1 | Generate seeded, repeatable incident scenarios with golden trajectories and expected resolution keywords | `evals/scenario_generator.py` |
| FR2 | Execute scenarios against an isolated mock environment per run | `evals/mock_infra.py` |
| FR3 | Run a ReAct agent with an iteration cap and span tracing | `agent_core.py`, `tracer.py` |
| FR4 | Score each run with deterministic guards, trajectory precision/recall, and a groundedness/safety judge | `evals/evaluators/` |
| FR5 | Compare aggregate metrics to a stored baseline and emit pass/fail plus a markdown report | `evals/eval_gate.py`, `scripts/run_ci_gate.py` |
| FR6 | Re-record the baseline only by explicit command | `--update-baseline` |
| FR7 | Block prohibited tools and screen arguments for injection | `evals/adversarial/guardrails.py` |
| FR8 | Require per-call approval for any non-read-only tool; deny by default; audit every decision | `approval_gate.py` |
| FR9 | Classify failures and attribute the pivot step in a trace | `evals/failure_classifier.py`, `evals/attribution_reporter.py` |
| FR10 | Support live Anthropic/OpenAI clients behind the same planner interface | `evals/clients.py` |

## 6. Non-functional requirements

- **Determinism:** same seed and code give the same metrics.
- **Safety:** zero tolerance on safety pass rate; unknown tools are treated as destructive.
- **Speed:** full test suite plus gate in CI under about 30 s.
- **Least privilege:** CI has `contents: read` (plus `pull-requests: write` for the report comment) and no provider keys.
- **Reproducibility:** all dependencies pinned.
- **Python:** 3.9+.

## 7. Success metrics

| Metric | Target |
| :--- | :--- |
| Gate false-fail rate on unchanged code | 0 (latency has a 5 ms noise floor) |
| Safety pass rate | 100% |
| Success-rate regression tolerated | at most 2 points |
| Tool-recall regression tolerated | at most 5 points |
| CI wall time | under 30 s |

## 8. Release status

| Milestone | State |
| :--- | :--- |
| Harness, gate, baseline, single CI workflow | Done |
| Offline playbook planner (non-tautological baseline) | Done |
| Approval gate in eval runner | Done |
| Approval gate on live demos and default agent registry | Open (P1) |
| Observation sanitization (indirect prompt injection) | Open (P1) |
| Hardened guardrail patterns, per-run mock state, run budget | Open (P1) |

Full list: [TODO.md](TODO.md).

## 9. Risks and assumptions

- **Assumption:** mock tools are a faithful enough proxy to detect regressions. Real tool behavior may differ.
- **Risk:** a baseline from a rule-based planner can mask LLM-specific failure modes. Mitigation: run live-model evaluations and record a model-specific baseline.
- **Risk:** regex guardrails are bypassable. Mitigation: they are one layer; the approval gate and least-privilege tools are independent layers.
- **Risk:** a small scenario set (2 incident types) overfits. Mitigation: expand the catalog (open work).
