"""evals/clients.py - Live LLM Provider Adapters (Anthropic & OpenAI) for OpsEvalGate."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional
from tracer import trace_span
from pathlib import Path 

from dotenv import load_dotenv

load_dotenv()  # Automatically looks for .env in current directory or parent dirs


# Auto-load .env if present
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.is_file():
  with open(env_file, "r", encoding="utf-8") as f:
    for line in f:
      line = line.strip()
      if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        if k.strip() not in os.environ:
          os.environ[k.strip()] = v.strip().strip("'\"")


# ---------------------------------------------------------------------------
# ReAct Prompt Scaffolding
# ---------------------------------------------------------------------------

REACT_SYSTEM_INSTRUCTIONS = """You are OpsEvalGate, an autonomous SRE triage and mitigation agent.
Solve the incident step-by-step using the ReAct (Reasoning + Action) paradigm.

Available Diagnostic Tools:
{tool_descriptions}

Rules:
1. At each step, analyze the incident description and all prior observations.
2. If further investigation or action is required, output a valid JSON object with:
   - "thought": String explaining your causal deduction and rationale.
   - "action": Name of the tool to execute (must match an available tool name exactly).
   - "action_input": Object containing tool parameter arguments.
3. Once the hanging database process is terminated or the incident is mitigated, do NOT re-query old logs. Output your resolution immediately as valid JSON with:
   - "thought": String explaining why mitigation is confirmed.
   - "final_answer": Detailed resolution summary citing the terminated PID and root cause.
4. Output strictly valid JSON.
"""


def _extract_json_payload(text: str) -> Dict[str, Any]:
  """Robust extraction of JSON objects from LLM responses."""
  cleaned = text.strip()
  if cleaned.startswith("```"):
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

  match = re.search(r"\{.*\}", cleaned, re.DOTALL)
  if match:
    return json.loads(match.group(0))
  return json.loads(cleaned)


def _format_scratchpad(scratchpad: Optional[List[Dict[str, Any]]]) -> str:
  if not scratchpad:
    return "(No previous steps executed. This is step 1.)"

  lines = []
  for step in scratchpad:
    lines.append(f"--- Step {step.get('step')} ---")
    lines.append(f"Thought: {step.get('thought')}")
    lines.append(f"Action: {step.get('action')}")
    lines.append(f"Action Input: {json.dumps(step.get('action_input'))}")
    lines.append(f"Observation: {json.dumps(step.get('observation'), default=str)}")
  return "\n".join(lines)


import inspect


def _describe_tools(tool_registry: Dict[str, Callable]) -> str:
  descriptions = []
  for name, fn in tool_registry.items():
    try:
      sig = inspect.signature(fn)
    except Exception:
      sig = "(...)"
    doc = (fn.__doc__ or "").strip() or "Execute diagnostic operation."
    descriptions.append(f"- `{name}{sig}`: {doc}")
  return "\n".join(descriptions)


# ---------------------------------------------------------------------------
# Anthropic Client Adapter
# ---------------------------------------------------------------------------


class AnthropicClientAdapter:
  """Adapter for Anthropic Claude (e.g., claude-3-5-sonnet, claude-3-5-haiku)."""

  def __init__(
      self,
      model: Optional[str] = None,
      api_key: Optional[str] = None,
      max_tokens: int = 1024,
      temperature: float = 0.0,
  ):
    from anthropic import Anthropic

    self.model = (
      model or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"
    )

    self.client = Anthropic(
        api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
    )
    
    self.max_tokens = max_tokens
    self.temperature = temperature

  def create_agent_planner(
      self, tool_registry: Dict[str, Callable]
  ) -> Callable[..., Dict[str, Any]]:
    tool_desc = _describe_tools(tool_registry)
    system_prompt = REACT_SYSTEM_INSTRUCTIONS.format(tool_descriptions=tool_desc)

    @trace_span(name="anthropic.reason_and_plan", span_kind="LLM")
    def planner(
        prompt: str,
        iteration: int = 1,
        scratchpad: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
      history_str = _format_scratchpad(scratchpad)
      user_message = (
          f"=== Incident Alert ===\n{prompt}\n\n"
          f"=== Current Scratchpad & Observations ===\n{history_str}\n\n"
          f"You are on iteration {iteration}. Output your next step JSON."
      )

      response = self.client.messages.create(
          model=self.model,
          max_tokens=self.max_tokens,
          temperature=self.temperature,
          system=system_prompt,
          messages=[{"role": "user", "content": user_message}],
      )

      raw_text = response.content[0].text
      try:
        return _extract_json_payload(raw_text)
      except Exception as err:
        return {
            "thought": f"Failed to parse model JSON output: {err}",
            "final_answer": raw_text,
        }

    return planner

  def create_judge_client(self) -> Callable[[str], str]:
    @trace_span(name="anthropic.judge_evaluate", span_kind="LLM")
    def judge(prompt: str) -> str:
      response = self.client.messages.create(
          model=self.model,
          max_tokens=self.max_tokens,
          temperature=self.temperature,
          messages=[{"role": "user", "content": prompt}],
      )
      return response.content[0].text

    return judge


# ---------------------------------------------------------------------------
# OpenAI Client Adapter
# ---------------------------------------------------------------------------


class OpenAIClientAdapter:
  """Adapter for OpenAI GPT models (e.g., gpt-4o, gpt-4o-mini, o3-mini)."""

  def __init__(
      self,
      model: str = "gpt-4o",
      api_key: Optional[str] = None,
      max_tokens: int = 1024,
      temperature: float = 0.0,
  ):
    from openai import OpenAI

    self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    self.model = model
    self.max_tokens = max_tokens
    self.temperature = temperature

  def create_agent_planner(
      self, tool_registry: Dict[str, Callable]
  ) -> Callable[..., Dict[str, Any]]:
    tool_desc = _describe_tools(tool_registry)
    system_prompt = REACT_SYSTEM_INSTRUCTIONS.format(tool_descriptions=tool_desc)

    @trace_span(name="openai.reason_and_plan", span_kind="LLM")
    def planner(
        prompt: str,
        iteration: int = 1,
        scratchpad: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
      history_str = _format_scratchpad(scratchpad)
      user_message = (
          f"=== Incident Alert ===\n{prompt}\n\n"
          f"=== Current Scratchpad & Observations ===\n{history_str}\n\n"
          f"You are on iteration {iteration}. Output your next step JSON."
      )

      # Use json_object response format on models that support it
      kwargs_api: Dict[str, Any] = {
          "model": self.model,
          "messages": [
              {"role": "system", "content": system_prompt},
              {"role": "user", "content": user_message},
          ],
      }
      if not self.model.startswith("o"):
        kwargs_api["temperature"] = self.temperature
        kwargs_api["max_tokens"] = self.max_tokens
        kwargs_api["response_format"] = {"type": "json_object"}

      response = self.client.chat.completions.create(**kwargs_api)
      raw_text = response.choices[0].message.content or "{}"

      try:
        return _extract_json_payload(raw_text)
      except Exception as err:
        return {
            "thought": f"Failed to parse model JSON output: {err}",
            "final_answer": raw_text,
        }

    return planner

  def create_judge_client(self) -> Callable[[str], str]:
    @trace_span(name="openai.judge_evaluate", span_kind="LLM")
    def judge(prompt: str) -> str:
      kwargs_api: Dict[str, Any] = {
          "model": self.model,
          "messages": [{"role": "user", "content": prompt}],
      }
      if not self.model.startswith("o"):
        kwargs_api["temperature"] = self.temperature
        kwargs_api["response_format"] = {"type": "json_object"}

      response = self.client.chat.completions.create(**kwargs_api)
      return response.choices[0].message.content or "{}"

    return judge