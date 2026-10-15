"""evals/cost/context_compactor.py - Observation compression & context window optimization."""

from __future__ import annotations
from dataclasses import dataclass, asdict
import json
import re
from typing import Any, Dict, List, Union


@dataclass
class CompactionResult:
    original_char_count: int
    compacted_char_count: int
    compression_ratio: float  # (original - compacted) / original
    truncated_items_count: int
    compacted_payload: Any

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContextCompactor:
    """Compacts tool observations (logs, SQL results, metrics) prior to LLM reinjection."""

    def __init__(
        self,
        max_log_lines: int = 5,
        max_db_rows: int = 5,
        max_field_length: int = 200,
    ):
        self.max_log_lines = max_log_lines
        self.max_db_rows = max_db_rows
        self.max_field_length = max_field_length

    def _truncate_text(self, text: str) -> str:
        if len(text) <= self.max_field_length:
            return text
        half = (self.max_field_length - 20) // 2
        return f"{text[:half]}...[TRUNCATED]...{text[-half:]}"

    def compact_log_entries(self, logs: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """Deduplicates and compacts recurring log entries."""
        if not logs:
            return [], 0

        compacted: List[Dict[str, Any]] = []
        seen_messages = set()
        truncated_count = 0

        for entry in logs:
            msg = entry.get("msg", "")
            # Normalize timestamps/uuids for deduplication
            normalized_msg = re.sub(r"\b[0-9a-fA-F-]{8,}\b", "<ID>", msg)

            if normalized_msg in seen_messages:
                truncated_count += 1
                continue

            seen_messages.add(normalized_msg)
            compact_entry = {
                "timestamp": entry.get("timestamp"),
                "service": entry.get("service"),
                "level": entry.get("level"),
                "msg": self._truncate_text(msg),
            }
            compacted.append(compact_entry)

            if len(compacted) >= self.max_log_lines:
                remaining = len(logs) - len(compacted)
                if remaining > 0:
                    truncated_count += remaining
                break

        return compacted, truncated_count

    def compact_database_rows(self, rows: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
        """Limits database telemetry table output to essential diagnostic rows and columns."""
        if not rows:
            return [], 0

        truncated_count = max(0, len(rows) - self.max_db_rows)
        retained = rows[:self.max_db_rows]

        compacted: List[Dict[str, Any]] = []
        for r in retained:
            compact_row = {}
            for k, v in r.items():
                if isinstance(v, str):
                    compact_row[k] = self._truncate_text(v)
                else:
                    compact_row[k] = v
            compacted.append(compact_row)

        return compacted, truncated_count

    def compact_observation(self, observation: Any) -> CompactionResult:
        """Entry point: analyzes observation payload and applies optimal compression."""
        orig_str = json.dumps(observation, default=str)
        orig_len = len(orig_str)

        compacted_payload: Any = observation
        truncated_items = 0

        if isinstance(observation, list):
            if observation and isinstance(observation[0], dict):
                # Distinguish logs vs DB rows
                if "msg" in observation[0]:
                    compacted_payload, truncated_items = self.compact_log_entries(observation)
                else:
                    compacted_payload, truncated_items = self.compact_database_rows(observation)
            elif len(observation) > self.max_db_rows:
                compacted_payload = observation[:self.max_db_rows]
                truncated_items = len(observation) - self.max_db_rows

        elif isinstance(observation, str):
            compacted_payload = self._truncate_text(observation)
            if len(compacted_payload) < orig_len:
                truncated_items = 1

        compacted_str = json.dumps(compacted_payload, default=str)
        comp_len = len(compacted_str)
        ratio = round((orig_len - comp_len) / orig_len, 4) if orig_len > 0 else 0.0

        return CompactionResult(
            original_char_count=orig_len,
            compacted_char_count=comp_len,
            compression_ratio=max(0.0, ratio),
            truncated_items_count=truncated_items,
            compacted_payload=compacted_payload,
        )