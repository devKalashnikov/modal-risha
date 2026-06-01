"""FLF-pair coherence — checks whether two generated keyframes are close enough
for an interpolation video model to bridge them cleanly."""

from __future__ import annotations

import json
import re


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class RishaPairCoherencePrompt:
    """Fill the coherence rubric with the expected adjacent_delta."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "rubric_template": ("STRING", {"multiline": True, "forceInput": True}),
                "adjacent_delta": ("STRING", {"multiline": True, "forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("coherence_prompt",)
    FUNCTION = "build"
    CATEGORY = "Risha/Pair"

    def build(self, rubric_template: str, adjacent_delta: str):
        filled = rubric_template.replace("{{adjacent_delta}}", adjacent_delta or "")
        filled = re.sub(r"\{\{[^}]*\}\}", "", filled)
        return (filled,)


def _extract_json(text: str):
    if not text:
        raise ValueError("Empty coherence output")
    stripped = _THINK_RE.sub("", text).strip()
    m = _FENCE_RE.search(stripped)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start == -1:
        raise ValueError("No JSON in coherence output")
    depth = 0
    for i, ch in enumerate(stripped[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start:i + 1])
    raise ValueError("Unbalanced braces in coherence output")


class RishaPairCoherenceParser:
    """Parse coherence JSON into BOOL consistent + which_frame_to_regen string."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "vlm_output": ("STRING", {"multiline": True, "forceInput": True}),
            },
        }

    RETURN_TYPES = (
        "BOOLEAN", "BOOLEAN", "STRING", "STRING", "STRING",
    )
    RETURN_NAMES = (
        "consistent", "delta_realized", "which_frame_to_regen",
        "issues", "guidance",
    )
    FUNCTION = "parse"
    CATEGORY = "Risha/Pair"

    def parse(self, vlm_output: str):
        try:
            data = _extract_json(vlm_output)
        except Exception as e:
            return (False, False, "last",
                    f"[coherence parser error] {type(e).__name__}: {e}",
                    "Re-run; coherence VLM emitted invalid JSON.")

        consistent = bool(data.get("consistent", False))
        delta_realized = bool(data.get("delta_realized", False))
        which = str(data.get("which_frame_to_regen") or "none").lower()
        if which not in {"first", "last", "none"}:
            which = "last"
        issues = data.get("issues") or []
        if isinstance(issues, list):
            issues_s = " | ".join(
                f"[{i.get('dimension','?') if isinstance(i, dict) else '?'}] "
                f"{i.get('note','') if isinstance(i, dict) else i}"
                for i in issues
            )
        else:
            issues_s = str(issues)
        guidance = str(data.get("guidance") or "")
        return (consistent, delta_realized, which, issues_s, guidance)
