"""Rewriter wrapper — builds the QwenVL prompt and parses its JSON response
into a `rewritten_prompt` string the next generation cycle can use directly.

The rewriter itself is a QwenVL call (image + this prompt). This node builds
the prompt; a downstream node parses the response."""

from __future__ import annotations

import json
import re


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class RishaPromptRewriterPrompt:
    """DEPRECATED shell — kept for graph compatibility; use RishaRubricRender
    with rubric_name=QWENVL_REWRITER_RUBRIC. Provided here so legacy graphs
    that import RishaPromptRewriterPrompt don't crash."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "rubric_template": ("STRING", {"multiline": True, "forceInput": True}),
                "intent_prompt": ("STRING", {"multiline": True, "forceInput": True}),
                "issues": ("STRING", {"multiline": True, "forceInput": True}),
                "guidance": ("STRING", {"multiline": True, "forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("rewriter_prompt",)
    FUNCTION = "build"
    CATEGORY = "Risha/Judge"

    def build(self, rubric_template: str, intent_prompt: str,
              issues: str, guidance: str):
        filled = (rubric_template
                  .replace("{{intent_prompt}}", intent_prompt)
                  .replace("{{issues}}", issues)
                  .replace("{{guidance}}", guidance))
        # Strip any remaining placeholders
        filled = re.sub(r"\{\{[^}]*\}\}", "", filled)
        return (filled,)


def _extract_json(text: str):
    if not text:
        raise ValueError("Empty rewriter output")
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
        raise ValueError("No JSON object in rewriter output")
    depth = 0
    for i, ch in enumerate(stripped[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start:i + 1])
    raise ValueError("Unbalanced braces in rewriter output")


class RishaRewriterParser:
    """Parse QwenVL rewriter JSON into the rewritten prompt string + change log."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "vlm_output": ("STRING", {"multiline": True, "forceInput": True}),
                "fallback_prompt": ("STRING", {"multiline": True, "forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("rewritten_prompt", "changes_log")
    FUNCTION = "parse"
    CATEGORY = "Risha/Judge"

    def parse(self, vlm_output: str, fallback_prompt: str):
        try:
            data = _extract_json(vlm_output)
        except Exception:
            # Fall back to the original — safer than passing a broken string downstream.
            return (fallback_prompt, "[rewriter parse failed — reusing original intent_prompt]")
        rewritten = data.get("rewritten_prompt") or fallback_prompt
        changes = data.get("changes") or []
        if isinstance(changes, list):
            changes_s = " | ".join(str(c) for c in changes)
        else:
            changes_s = str(changes)
        return (str(rewritten), changes_s)


# Register the parser alongside the prompt builder
# (the builder module re-exports this to keep imports clean from __init__.py)
