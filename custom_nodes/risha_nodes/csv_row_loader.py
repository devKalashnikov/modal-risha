"""CSV row loader — reads one keyframe plan row and fans out as typed outputs.

The CSV is produced by Claude Desktop via the risha-script-to-keyframes skill.
One execution of the master workflow consumes ONE row, indexed by `row_index`.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS = [
    "keyframe_id", "timestamp_start", "timestamp_end", "narration_beat",
    "narration_text", "motion_intent", "pair_id", "pair_role",
    "generator_model", "intent_prompt", "composition_notes",
    "adjacent_delta", "handoff_tool",
]


def _read_rows(csv_path: str) -> list[dict[str, str]]:
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(f"Risha plan CSV not found: {csv_path}")
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"Plan CSV {csv_path} is missing required columns: {missing}. "
                f"Expected schema: {REQUIRED_COLUMNS}"
            )
        return [{k: (v if v is not None else "") for k, v in row.items()} for row in reader]


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return default


class RishaCSVRowLoader:
    """Reads one row from a Risha keyframe plan CSV.

    OUTPUTS (ordered):
        keyframe_id, timestamp_start, timestamp_end, narration_beat,
        narration_text, motion_intent, pair_id, pair_role, generator_model,
        intent_prompt, composition_notes, adjacent_delta, handoff_tool,
        is_diffused (BOOL), is_pair (BOOL), is_pair_first (BOOL)
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "csv_path": ("STRING", {
                    "default": "/root/comfy/ComfyUI/input/plans/latest.csv",
                    "multiline": False,
                }),
                "row_index": ("INT", {"default": 0, "min": 0, "max": 9999}),
            },
        }

    RETURN_TYPES = (
        "STRING",  # keyframe_id
        "FLOAT",   # timestamp_start
        "FLOAT",   # timestamp_end
        "STRING",  # narration_beat
        "STRING",  # narration_text
        "STRING",  # motion_intent
        "STRING",  # pair_id
        "STRING",  # pair_role
        "STRING",  # generator_model
        "STRING",  # intent_prompt
        "STRING",  # composition_notes
        "STRING",  # adjacent_delta
        "STRING",  # handoff_tool
        "BOOLEAN", # is_diffused
        "BOOLEAN", # is_pair
        "BOOLEAN", # is_pair_first
    )
    RETURN_NAMES = (
        "keyframe_id", "timestamp_start", "timestamp_end", "narration_beat",
        "narration_text", "motion_intent", "pair_id", "pair_role",
        "generator_model", "intent_prompt", "composition_notes",
        "adjacent_delta", "handoff_tool",
        "is_diffused", "is_pair", "is_pair_first",
    )
    FUNCTION = "load"
    CATEGORY = "Risha/Plan"

    def load(self, csv_path: str, row_index: int):
        rows = _read_rows(csv_path)
        if row_index >= len(rows):
            raise IndexError(
                f"row_index {row_index} out of range (plan has {len(rows)} rows)"
            )
        r = rows[row_index]
        motion = r["motion_intent"].strip().lower()
        pair_role = r["pair_role"].strip().lower()
        is_diffused = motion != "static_slide"
        is_pair = motion == "flf_pair"
        is_pair_first = is_pair and pair_role == "first"
        return (
            r["keyframe_id"].strip(),
            _to_float(r["timestamp_start"]),
            _to_float(r["timestamp_end"]),
            r["narration_beat"].strip(),
            r["narration_text"],
            motion,
            r["pair_id"].strip(),
            pair_role,
            r["generator_model"].strip().lower(),
            r["intent_prompt"],
            r["composition_notes"].strip(),
            r["adjacent_delta"],
            r["handoff_tool"].strip().lower(),
            is_diffused,
            is_pair,
            is_pair_first,
        )

    @classmethod
    def IS_CHANGED(cls, csv_path: str, row_index: int):
        """Invalidate cache when CSV content or row_index changes."""
        p = Path(csv_path)
        if not p.exists():
            return f"missing:{csv_path}:{row_index}"
        return f"{csv_path}:{p.stat().st_mtime_ns}:{row_index}"


class RishaCSVRowCount:
    """Return the number of rows in a plan CSV. Used by the Plan Executor to
    know how many times to queue the master workflow."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "csv_path": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("row_count",)
    FUNCTION = "count"
    CATEGORY = "Risha/Plan"

    def count(self, csv_path: str):
        return (len(_read_rows(csv_path)),)

    @classmethod
    def IS_CHANGED(cls, csv_path: str):
        p = Path(csv_path)
        if not p.exists():
            return f"missing:{csv_path}"
        return f"{csv_path}:{p.stat().st_mtime_ns}"
