# OpsEvalGate

[![CI Eval Gate](https://github.com/swathikchgithub/OpsEvalGate/actions/workflows/eval_gate.yml/badge.svg)](https://github.com/swathikchgithub/OpsEvalGate/actions/workflows/eval_gate.yml)
[![Tests](https://img.shields.io/badge/tests-102%2F102%20passing-brightgreen)](tests/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**OpsEvalGate** is an enterprise-grade autonomous LLM agent framework designed for Site Reliability Engineering (SRE) incident investigation and remediation. It pairs a deterministic ReAct execution loop with least-privilege security guardrails, OpenInference telemetry, and automated CI/CD evaluation gates.

---

## Key Highlights

- **ReAct Core with Trace Instrumentation:** Multi-step autonomous reasoning using OpenAI (`gpt-4o`, `gpt-4o-mini`) or Anthropic (`claude-sonnet-4-5`), traced with OpenInference spans.
- **Least-Privilege Guardrails:** Interception layer enforcing prohibited tools, shell/SQL command injection defense, and indirect prompt injection resistance (log poisoning).
- **Synthetic Incident Corpus:** Deterministic generation of multi-persona SRE incidents (Sev1/Sev2, connection pool exhaustion, deadlock contention, token starvation).
- **Comprehensive Evaluation Harness:** Trajectory precision/recall, deterministic output assertions, calibrated LLM judges (Cohen's Kappa $\ge 0.70$), and QuAIL transition graph verification.
- **CI/CD Regression Gate:** Automated GitHub Actions pull request gate that blocks regressions in tool accuracy, safety violations, or latency deltas.

---

## Repository Layout

```text
opsevalgate/
├── .github/workflows/
│   └── eval_gate.yml          # GitHub Actions PR evaluation gate
├── agent_core.py              # ReAct agent loop, tool execution & registry
├── approval_gate.py           # Human-in-the-loop approval gate for destructive tools
├── tracer.py                  # OpenInference trace decorator & context manager
├── run_adversarial_demo.py    # Live attack simulation runner
├── scripts/
│   └── run_ci_gate.py         # CI evaluation runner and markdown PR reporter
├── evals/
│   ├── adversarial/           # Guardrails, injection corpus & safety evaluators
│   ├── clients.py             # OpenAI & Anthropic model client adapters
│   ├── eval_gate.py           # Regression gate logic & delta calculation
│   ├── runner.py              # Batch scenario evaluation harness
│   └── scenario_generator.py  # Synthetic incident scenario engine
├── tests/                     # 102-test comprehensive pytest test suite
└── docs/                      # Architectural, design, PRD, and workflow specs