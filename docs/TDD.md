# Technical Design Document (TDD)

## System Internals & Engineering Specification

### 1. Core Modules & Data Models

#### 1.1 Agent Execution Engine (`agent_core.py`)
The autonomous agent follows an iterative ReAct pattern:
$$\text{State}_{t} = \text{Planner}(\text{Prompt}, \text{History}_{t-1})$$
$$\text{Observation}_{t} = \text{Guardrail}(\text{Action}_{t}, \text{Args}_{t})$$

- **`ReActAgent`:** Coordinates the iteration loop, tracks the scratchpad trajectory, and terminates upon receiving a `Final Resolution` or hitting `max_iterations`.
- **`TOOL_REGISTRY`:** Dictionary of executable functions (`query_logs`, `query_database_state`, `kill_db_process`, `restart_service`).

#### 1.2 Instrumentation & Telemetry (`tracer.py`)
- **OpenInference Spans:** Every agent iteration, LLM prompt/response cycle, and tool invocation emits an OpenInference-compliant span (`SpanKind.CHAIN`, `SpanKind.LLM`, `SpanKind.TOOL`).
- **`GLOBAL_TRACER`:** In-memory collector enabling instant trace inspection, session serialization, and offline evaluation.

---

### 2. Guardrail Subsystem (`evals/adversarial/guardrails.py`)

The guardrail acts as an inline proxy wrapping the base tool registry:

```text
Model Tool Call
      │
      ▼
┌──────────────────────────────────────────────┐
│        ToolExecutionGuardrail                │
│                                              │
│ 1. Check Tool Name in Prohibited Set?        │──[Yes]──> Raise SecurityViolation / Block
│ 2. Inspect Arguments for Regex Injections?   │──[Yes]──> Raise SecurityViolation / Block
└──────────────────────────────────────────────┘
      │ [Passed]
      ▼
Target Tool Execution