"""observation_sanitizer.py - Defense against indirect prompt injection in tool output.

Tool results (log lines, DB fields) are attacker-influenced data. Before they
enter the agent's scratchpad this module:
  * strips control and invisible characters that can hide instructions,
  * neutralizes the `<tool_output>` delimiter so content cannot close the data block,
  * bounds size, nesting and total work (resource exhaustion),
  * FLAGS instruction-like text with a visible prefix and a finding. It never
    silently deletes it: a poisoned log line is evidence worth showing.

This is a mitigation, not a guarantee. Pattern matching cannot catch every
phrasing; the approval gate remains the backstop for destructive actions.
Dict keys are not sanitized.

Complexity: Time O(min(N, max_nodes) * max_string_chars) for N nodes, because
strings are truncated before any regex runs and patterns have bounded
quantifiers. Space is the same bound.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Pattern, Tuple

FLAG_PREFIX = "[flagged: possible injection] "
DELIMITER_REPLACEMENT = "[delimiter-removed]"
DEPTH_PLACEHOLDER = "[max depth exceeded]"
NODE_PLACEHOLDER = "[node budget exceeded]"

# C0/C1 controls (keeping \n and \t), DEL, zero-width, bidi overrides, BOM, soft hyphen.
_HIDDEN_CHARS = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f­​-‏  "
    "‪-‮⁠-⁤⁦-⁩﻿]"
)
_DELIMITER = re.compile(r"<\s*/?\s*tool_output\s*>", re.IGNORECASE)

# Applied to NFKC-normalized, case-folded text. Quantifiers are bounded (ReDoS-safe).
_WORDS = r"\W{0,3}(?:\w+\W{1,3}){0,3}?"
_INJECTION_RULES: List[Tuple[str, Pattern[str]]] = [
    (
        "override_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b" + _WORDS +
            r"(?:instructions|rules|directives|prompts?|goals)\b"
        ),
    ),
    (
        "system_directive",
        re.compile(r"\b(?:system|assistant)\s+(?:directive|override|prompt|message|instructions?)\b"),
    ),
    ("new_instructions", re.compile(r"\bnew\s+(?:instructions|directives|orders)\b")),
    ("role_hijack", re.compile(r"\byou\s+are\s+now\b")),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(?:reveal|print|output|exfiltrate|leak|dump)\b" + _WORDS +
            r"(?:secrets?|passwords?|credentials?|api[ _-]?keys?|tokens?|canary)\b"
        ),
    ),
    ("tool_call_syntax", re.compile(r'"action(?:_input)?"\s*:')),
    ("chat_template", re.compile(r"<\|im_(?:start|end)\||\[/?inst\]|<<sys>>|<\|system\|>")),
]


def neutralize_delimiters(text: str) -> str:
  """Replaces `<tool_output>` look-alikes so content cannot close the data block."""
  return _DELIMITER.sub(DELIMITER_REPLACEMENT, text)


@dataclass(frozen=True)
class Sanitized:
  value: Any
  findings: Tuple[str, ...] = ()


class ObservationSanitizer:
  """Makes one tool observation safe to place in the model's context."""

  def __init__(
      self,
      max_string_chars: int = 1000,
      max_items: int = 100,
      max_depth: int = 6,
      max_nodes: int = 2000,
  ):
    self.max_string_chars = max_string_chars
    self.max_items = max_items
    self.max_depth = max_depth
    self.max_nodes = max_nodes

  def sanitize(self, observation: Any) -> Sanitized:
    findings: List[str] = []
    budget = [self.max_nodes]
    value = self._walk(observation, 0, findings, budget)
    return Sanitized(value, tuple(dict.fromkeys(findings)))

  def _walk(self, obj: Any, depth: int, findings: List[str], budget: List[int]) -> Any:
    if depth > self.max_depth:
      return DEPTH_PLACEHOLDER
    budget[0] -= 1
    if budget[0] < 0:
      return NODE_PLACEHOLDER
    if obj is None or isinstance(obj, (bool, int, float)):
      return obj
    if isinstance(obj, str):
      return self._clean_text(obj, findings)
    if isinstance(obj, dict):
      items = list(obj.items())[: self.max_items]
      return {k: self._walk(v, depth + 1, findings, budget) for k, v in items}
    if isinstance(obj, (list, tuple)):
      return [self._walk(v, depth + 1, findings, budget) for v in list(obj)[: self.max_items]]
    return self._clean_text(str(obj), findings)

  def _clean_text(self, text: str, findings: List[str]) -> str:
    text = _HIDDEN_CHARS.sub("", text)
    dropped = len(text) - self.max_string_chars
    if dropped > 0:
      text = text[: self.max_string_chars] + f"...[truncated {dropped} chars]"
    text = neutralize_delimiters(text)
    matched = self._match_rules(text)
    if not matched:
      return text
    findings.extend(matched)
    return FLAG_PREFIX + text

  @staticmethod
  def _match_rules(text: str) -> List[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [name for name, pattern in _INJECTION_RULES if pattern.search(normalized)]
