"""Append an attempt record to a per-video JSON log on disk.

The log is a JSON array; each entry records one (keyframe_id, attempt_n)
outcome — scores, issues, guidance, rewritten prompt, wall time, seed.
Inspectable in ComfyUI's file browser or by cat-ing the file post-run."""

from __future__ import annotations

import json
import time
from pathlib import Path


class RishaAttemptLogger:
    """Append a log entry. Outputs the log file path for downstream chaining."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "log_path": ("STRING", {
                    "default": "/root/comfy/ComfyUI/output/plan_attempts.json"
                }),
                "keyframe_id": ("STRING", {"forceInput": True}),
                "attempt_n": ("INT", {"default": 1, "min": 1, "max": 99}),
                "stage": ("STRING", {"default": "t2i"}),  # t2i | rewrite | pair_check | edit
                "model": ("STRING", {"default": ""}),
                "intent_prompt": ("STRING", {"multiline": True, "default": ""}),
                "overall_score": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 10.0}),
                "passed": ("BOOLEAN", {"default": False}),
                "issues": ("STRING", {"multiline": True, "default": ""}),
                "guidance": ("STRING", {"multiline": True, "default": ""}),
                "duration_s": ("FLOAT", {"default": 0.0}),
                "seed": ("INT", {"default": 0}),
                "scores_json": ("STRING", {"multiline": True, "default": "{}"}),
                "output_image_path": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("log_path",)
    FUNCTION = "log"
    CATEGORY = "Risha/Logging"
    OUTPUT_NODE = True  # ensures ComfyUI actually executes the node

    def log(self, log_path: str, keyframe_id: str, attempt_n: int, stage: str,
            model: str, intent_prompt: str, overall_score: float,
            passed: bool, issues: str, guidance: str, duration_s: float,
            seed: int, scores_json: str, output_image_path: str):
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        entries: list = []
        if p.exists():
            try:
                entries = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = []
            except Exception:
                entries = []

        try:
            scores = json.loads(scores_json) if scores_json else {}
        except Exception:
            scores = {"_raw": scores_json}

        entry = {
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "keyframe_id": keyframe_id,
            "attempt_n": attempt_n,
            "stage": stage,
            "model": model,
            "intent_prompt_preview": (intent_prompt[:500] +
                                       ("…" if len(intent_prompt) > 500 else "")),
            "overall_score": overall_score,
            "passed": passed,
            "issues": issues,
            "guidance_preview": (guidance[:300] +
                                  ("…" if len(guidance) > 300 else "")),
            "duration_s": duration_s,
            "seed": seed,
            "scores": scores,
            "output_image_path": output_image_path,
        }
        entries.append(entry)
        p.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return (str(p),)
