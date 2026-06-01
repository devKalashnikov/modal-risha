"""Parse QwenVL's judge-rubric text output into typed ComfyUI values.

QwenVL-Thinking sometimes emits `<think>...</think>` before the JSON, and
may wrap the JSON in ```json fences. This parser is tolerant of both and
falls back to a first-brace heuristic when regex fails."""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> dict[str, Any]:
    if not text:
        raise ValueError("Empty VLM output — cannot parse judge JSON")

    stripped = _THINK_RE.sub("", text).strip()

    # Try fenced JSON first
    m = _FENCE_RE.search(stripped)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Try raw JSON at start
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Last resort: find first {...} block with balanced braces
    start = stripped.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in VLM output:\n{stripped[:500]}")
    depth = 0
    for i, ch in enumerate(stripped[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                block = stripped[start:i + 1]
                return json.loads(block)
    raise ValueError(f"Unbalanced braces in VLM output:\n{stripped[:500]}")


class RishaJudgeParser:
    """Parse a judge rubric response into scores + pass decision.

    Inputs:
        vlm_output — raw text from QwenVL node
        pass_threshold — overall score required to pass (default 7.0)
        min_dim_floor — any dimension below this forces pass=false (default 5)
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "vlm_output": ("STRING", {"multiline": True, "forceInput": True}),
                "pass_threshold": ("FLOAT", {"default": 7.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "min_dim_floor": ("INT", {"default": 5, "min": 0, "max": 10}),
            },
        }

    RETURN_TYPES = (
        "FLOAT",   # overall
        "BOOLEAN", # passed
        "STRING",  # issues (joined with " | ")
        "STRING",  # guidance
        "STRING",  # scores_json (full scores dict as JSON for logging)
        "STRING",  # raw_parsed (pretty-printed full parsed JSON, for debugging)
    )
    RETURN_NAMES = ("overall", "passed", "issues", "guidance", "scores_json", "raw_parsed")
    FUNCTION = "parse"
    CATEGORY = "Risha/Judge"

    def parse(self, vlm_output: str, pass_threshold: float, min_dim_floor: int):
        try:
            data = _extract_json(vlm_output)
        except Exception as e:
            # Graceful degradation — parser failure = forced retry at overall=0
            err = f"[judge parser error] {type(e).__name__}: {e}"
            return (0.0, False, err, "Re-run; the judge emitted invalid JSON.",
                    "{}", err)

        scores = data.get("scores", {}) or {}
        overall = data.get("overall")
        if overall is None and scores:
            overall = sum(v for v in scores.values() if isinstance(v, (int, float))) / max(len(scores), 1)
        overall_f = float(overall or 0.0)

        min_dim = min(
            (v for v in scores.values() if isinstance(v, (int, float))),
            default=0,
        )
        passed = (overall_f >= pass_threshold) and (min_dim >= min_dim_floor)
        # Respect the VLM's own `pass` field when present (if it says fail, trust it)
        if isinstance(data.get("pass"), bool) and data["pass"] is False:
            passed = False

        issues_list = data.get("issues") or []
        if isinstance(issues_list, list):
            issues = " | ".join(str(x) for x in issues_list)
        else:
            issues = str(issues_list)

        guidance = str(data.get("guidance") or "")

        return (
            overall_f,
            bool(passed),
            issues,
            guidance,
            json.dumps(scores, ensure_ascii=False),
            json.dumps(data, ensure_ascii=False, indent=2),
        )
