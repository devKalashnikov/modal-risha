"""Rubric loader + template render — reads rubric .md files and fills the
{{placeholders}} with per-call context. The filled string becomes the
custom_prompt input to a QwenVL node."""

from __future__ import annotations

import re
from pathlib import Path


class RishaRubricLoader:
    """Load a rubric markdown file by name from the rubrics/ directory.

    Rubrics live outside the node pack so they can be edited at runtime
    without restarting ComfyUI. Default search paths (in order):
      1. /root/comfy/ComfyUI/input/rubrics/
      2. /root/comfy/ComfyUI/custom_nodes/risha_nodes/rubrics/  (fallback bundled)
      3. The directory passed via rubrics_dir override
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "rubric_name": ([
                    "QWENVL_JUDGE_RUBRIC",
                    "QWENVL_REWRITER_RUBRIC",
                    "QWENVL_COHERENCE_RUBRIC",
                ], {"default": "QWENVL_JUDGE_RUBRIC"}),
            },
            "optional": {
                "rubrics_dir": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("rubric_template",)
    FUNCTION = "load"
    CATEGORY = "Risha/Rubric"

    def load(self, rubric_name: str, rubrics_dir: str = ""):
        candidates: list[Path] = []
        if rubrics_dir:
            candidates.append(Path(rubrics_dir))
        candidates.append(Path("/root/comfy/ComfyUI/input/rubrics"))
        candidates.append(Path(__file__).parent / "rubrics")

        filename = f"{rubric_name}.md"
        for d in candidates:
            p = d / filename
            if p.exists():
                return (p.read_text(encoding="utf-8"),)
        searched = " | ".join(str(d / filename) for d in candidates)
        raise FileNotFoundError(f"Rubric {rubric_name} not found. Searched: {searched}")

    @classmethod
    def IS_CHANGED(cls, rubric_name: str, rubrics_dir: str = ""):
        # Re-load every queue — rubrics are small and we want edits live.
        import time
        return f"{rubric_name}:{rubrics_dir}:{time.time_ns()}"


class RishaRubricRender:
    """Fill a rubric template's {{placeholder}} slots with provided values.

    Up to 5 key/value slots, each comma-or-newline-separated key=value.
    Unknown placeholders are left as empty strings (not errors — the rubric
    may define optional slots).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "template": ("STRING", {"multiline": True, "forceInput": True}),
            },
            "optional": {
                "key1": ("STRING", {"default": "intent_prompt"}),
                "val1": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key2": ("STRING", {"default": "composition_notes"}),
                "val2": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key3": ("STRING", {"default": "issues"}),
                "val3": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key4": ("STRING", {"default": "guidance"}),
                "val4": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key5": ("STRING", {"default": "adjacent_delta"}),
                "val5": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filled",)
    FUNCTION = "render"
    CATEGORY = "Risha/Rubric"

    def render(self, template: str,
               key1: str = "", val1: str = "",
               key2: str = "", val2: str = "",
               key3: str = "", val3: str = "",
               key4: str = "", val4: str = "",
               key5: str = "", val5: str = ""):
        pairs: dict[str, str] = {}
        for k, v in [(key1, val1), (key2, val2), (key3, val3), (key4, val4), (key5, val5)]:
            k = (k or "").strip()
            if k:
                pairs[k] = v or ""
        out = template
        # Replace known placeholders
        for k, v in pairs.items():
            out = out.replace("{{" + k + "}}", v)
        # Clear any remaining placeholders so we don't ship literal {{x}} to QwenVL
        out = re.sub(r"\{\{[^}]*\}\}", "", out)
        return (out,)
