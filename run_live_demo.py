"""run_live_demo.py - Run OpsEvalGate and LLMJudge against live model APIs."""

import os
from dotenv import load_dotenv

# Ensure .env is loaded before accessing keys
load_dotenv()

from agent_core import ReActAgent, TOOL_REGISTRY
from approval_gate import ApprovalGate, ConsoleApprover
from evals.clients import AnthropicClientAdapter, OpenAIClientAdapter
from evals.evaluators.llm_judge import LLMJudge
from tracer import GLOBAL_TRACER


def run_live(preferred_provider: str = "auto"):
    """Runs a live end-to-end incident triage and model evaluation.

    preferred_provider can be 'openai', 'anthropic', or 'auto'.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if preferred_provider == "anthropic" or (
        preferred_provider == "auto" and anthropic_key and not openai_key
    ):
        model_name = os.environ.get(
            "ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"
        )
        print(f"Using Anthropic Provider ({model_name})...")
        adapter = AnthropicClientAdapter(model=model_name)
    elif preferred_provider in ("openai", "auto") and openai_key:
        model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        print(f"Using OpenAI Provider ({model_name})...")
        adapter = OpenAIClientAdapter(model=model_name)
    elif anthropic_key:
        model_name = os.environ.get(
            "ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"
        )
        print(f"Using Anthropic Provider ({model_name})...")
        adapter = AnthropicClientAdapter(model=model_name)
    else:
        raise EnvironmentError(
            "Please ensure ANTHROPIC_API_KEY or OPENAI_API_KEY is defined in .env"
        )

    # 1. Wire live planner into ReActAgent. A human approves each destructive
    # call at the terminal; read-only tools run unattended.
    gated_tools = ApprovalGate(ConsoleApprover()).wrap_all(TOOL_REGISTRY)
    planner = adapter.create_agent_planner(tool_registry=gated_tools)
    agent = ReActAgent(
        tools=gated_tools, model_client=planner, max_iterations=6
    )

    incident = (
        "CRITICAL: checkout-api reports pool exhausted error. Upstream requests"
        " timing out with HTTP 500."
    )
    print(f"\n[Running Incident Triage]: {incident}\n")

    GLOBAL_TRACER.clear()
    result = agent.run(incident)

    print("=" * 60)
    print(f"Agent Status:     {result['status']}")
    print(f"Steps Executed:   {result['steps_executed']}")
    print(f"Final Resolution: {result['resolution']}")
    print("=" * 60)
    print("Trajectory:")
    for step in result.get("trajectory", []):
      print(f"  Step {step['step']}: {step.get('action')}")
      print(f"    Thought: {step.get('thought')}")
    print("=" * 60)

    # 2. Wire live judge client into LLMJudge
    judge_client = adapter.create_judge_client()
    judge = LLMJudge(model_client=judge_client)

    session_data = {
        "incident_id": "LIVE-DEMO-001",
        "result": result,
        "spans": [s.to_dict() for s in GLOBAL_TRACER.spans],
    }

    print("\n[Running Model-Graded Evaluation]...")
    eval_res = judge.evaluate(session_data, incident_prompt=incident)

    print(f"Judge Passed:       {eval_res.passed}")
    print(f"Groundedness Score: {eval_res.groundedness_score}/5")
    print(f"Safety Score:       {eval_res.safety_score}/5")
    print(f"Judge Critique:     {eval_res.critique}")


if __name__ == "__main__":
    run_live()