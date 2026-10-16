# OpsEvalGate

[![CI Eval Gate](https://github.com/swathikchgithub/OpsEvalGate/actions/workflows/eval_gate.yml/badge.svg)](https://github.com/swathikchgithub/OpsEvalGate/actions/workflows/eval_gate.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**OpsEvalGate** is a reference framework for building and *regression-testing* an LLM agent that triages SRE incidents. It has three parts:

1. a small **ReAct agent** (reason, call a tool, observe, repeat) over mock SRE tools;
2. an **evaluation harness** that replays synthetic incidents and scores every run;
3. a **CI regression gate** that fails a pull request when quality, safety or latency gets worse than a stored baseline.

> Not affiliated with the *OpsEval* IT-operations LLM benchmark (FSE 2025) or with similarly named "EvalGate" projects.

## What this is, and what it is not

| It is | It is not |
| :--- | :--- |
| A working pipeline: agent, scorers, baseline, gate, all running in CI with no API keys | A production incident-response product |
| Deterministic and reproducible (seeded scenarios, rule-based offline planner) | A benchmark of any LLM's SRE ability |
| A place to prove a guardrail or prompt change did not regress behavior | Connected to real infrastructure: every tool acts on an in-memory mock |

The checked-in baseline is produced by an **offline rule-based planner** (`evals/offline_planner.py`), so it measures the *pipeline and rules*, not a model. Point the harness at a live model (`run_live_demo.py`) to evaluate one.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # core: pydantic, pytest, python-dotenv

pytest                                     # unit + integration suite
python scripts/run_ci_gate.py              # run the gate against the stored baseline (exit 0 = pass)
python scripts/run_ci_gate.py --update-baseline   # re-record the baseline (review the diff!)
```

Live-model demos are optional and need keys:

```bash
pip install -r requirements-llm.txt        # anthropic, openai
cp .env.example .env                       # then fill in keys; .env is git-ignored
python run_live_demo.py
python run_adversarial_demo.py
```

> The live demos currently run **without** the approval gate. Do not point them at anything real. See [Known limitations](#known-limitations).

## How the CI gate works

```text
PR / push to main
   └─ pytest tests/
   └─ scripts/run_ci_gate.py
        ├─ generate 10 seeded scenarios (seed 42)
        ├─ run each through the agent + 3 evaluators
        ├─ compare metrics with evals/baselines/baseline_metrics.json
        └─ fail on any of:
             safety pass rate < 100%        (zero tolerance)
             success rate drop  > 2 pts
             tool recall drop   > 5 pts
             latency +30% AND > 5 ms        (noise floor)
```

CI uses no secrets and no network calls to model providers.

## Security model (summary)

- **Tool guardrail** blocks a deny-list of tools (shell, `drop_table`, ...) and screens string arguments for injection patterns.
- **Approval gate** (`approval_gate.py`) is fail-closed: only `query_logs` and `query_database_state` run unattended. Everything else, including tools added later, needs an explicit human or policy approval *per call*. Eval runs use a sandbox auto-approver; the default approver denies.
- **Tracing** records every step as an OpenInference-style span for audit and replay.

Details and threat discussion: [docs/DESIGN.md](docs/DESIGN.md).

## Repository layout

```text
agent_core.py            ReAct loop + mock tools (global registry)
agent_router.py          Complexity-based model-tier router (heuristic)
approval_gate.py         Human-in-the-loop gate for destructive tools
tracer.py, trace_replay.py   Span capture, session (de)serialization
evals/
  scenario_generator.py  Seeded synthetic incidents
  mock_infra.py          Per-scenario sandboxed mock environment
  offline_planner.py     Rule-based "playbook" planner (no LLM)
  runner.py              Runs scenarios, aggregates metrics
  evaluators/            deterministic guards, trajectory metrics, LLM judge, Quail step judge, Kappa calibration
  adversarial/           guardrails, injection corpus, safety evaluator
  eval_gate.py           Regression gate + PR comment markdown
  failure_classifier.py, attribution_reporter.py, trace_parser.py   post-mortem tooling
  cost/                  cache profiler, context compactor, Pareto benchmark
  clients.py             Live Anthropic / OpenAI adapters
scripts/run_ci_gate.py   CI entry point
tests/                   pytest suite
docs/                    PRD, architecture, decisions, design, TDD, test-first workflow, TODO
```

## Documentation

| Doc | Purpose |
| :--- | :--- |
| [docs/PRD.md](docs/PRD.md) | Problem, users, goals, requirements, success metrics |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System structure, data flow, and **why this architecture** (decision records) |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decision log (ADRs): why each major choice was made |
| [docs/DESIGN.md](docs/DESIGN.md) | Component design, security design, extension points |
| [docs/TEST_DRIVEN_DEVELOPMENT.md](docs/TEST_DRIVEN_DEVELOPMENT.md) | Test-first workflow, conventions and definition of done |
| [docs/TDD.md](docs/TDD.md) | Technical design: module contracts, metrics, test strategy |
| [docs/TODO.md](docs/TODO.md) | Prioritized open work |

## Known limitations

- The agent's default tool registry (`agent_core.TOOL_REGISTRY`) is a global mutable mock and is **not** approval-gated; only the eval runner applies the gate.
- Guardrail regexes are heuristic: some false positives (`--`, `;`) and known bypasses (newlines, `$IFS`, URL-encoding).
- Tool output (e.g. log lines) is not yet sanitized before it enters the prompt, so indirect prompt injection is **not** mitigated.
- The scenario catalog has two incident types; the baseline covers 10 scenarios.
- The LLM judge in CI is a deterministic stand-in; Kappa calibration code exists but is exercised only on supplied ratings.

See [docs/TODO.md](docs/TODO.md) for the plan.

## License

MIT. See [LICENSE](LICENSE).
