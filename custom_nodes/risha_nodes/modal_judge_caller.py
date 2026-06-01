"""RishaModalJudge — call the deployed risha-judge Modal app from inside ComfyUI.

Cross-app Modal RPC: this runs in the risha-comfyui container and reaches over
to the risha-judge container via `modal.Cls.from_name(...).judge.remote(...)`.
Both apps live in the same Modal workspace, so no extra auth is needed.

Inputs:
    image      — IMAGE tensor from VAEDecode / LoadImage (any source)
    judge_name — qwen8b | qwen32b | kimi (selects the deployed Cls)
    intent     — STRING, the intent_prompt this image was supposed to satisfy
    attempt    — INT, attempt number for logging context

Outputs:
    verdict_json — STRING, pretty-printed JSON of the judge's full Verdict dict.
                   Wire to a Display Any (rgthree) node to see it in the UI.
    score        — FLOAT, the 0-10 score (for downstream switches)
    verdict      — STRING, "pass" | "edit" | "regen" (for downstream routing)
    next_prompt  — STRING, the editor or regen prompt (empty if pass)
"""

import base64
import io
import json
import time
from typing import Any


class RishaModalJudge:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image":   ("IMAGE",),
                "intent":  ("STRING", {"multiline": True, "default": ""}),
                "attempt": ("INT", {"default": 1, "min": 1, "max": 10}),
            },
            "optional": {
                "app_name": ("STRING", {"default": "risha-judge"}),
                "cls_name": ("STRING", {"default": "JudgeQwen32B"}),
            },
        }

    RETURN_TYPES = ("STRING", "FLOAT", "STRING", "STRING")
    RETURN_NAMES = ("verdict_json", "score", "verdict", "next_prompt")
    FUNCTION = "judge"
    CATEGORY = "Risha"
    OUTPUT_NODE = True  # so it always runs even if outputs aren't consumed

    def _encode_png_b64(self, image_tensor) -> str:
        """ComfyUI IMAGE tensor [B,H,W,C] in [0,1] float → base64 PNG bytes."""
        from PIL import Image
        import numpy as np
        # take the first image in the batch
        arr = image_tensor[0].detach().cpu().numpy()
        arr = (arr * 255.0).clip(0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    async def judge(self, image, intent: str, attempt: int,
                    app_name: str = "risha-judge",
                    cls_name: str = "JudgeQwen32B"):
        # ComfyUI's executor runs node functions inside an asyncio loop. Using
        # the sync `.remote()` blocks the loop and emits AsyncUsageWarning.
        # Switching to `async def` + `await .remote.aio()` is the supported path.
        try:
            import modal
        except ImportError as e:
            err = {"error": "modal SDK not installed in ComfyUI container",
                   "detail": str(e)}
            return (json.dumps(err, indent=2), 0.0, "regen", "")

        # ComfyUI passes "" when an optional widget is left blank in the UI;
        # fall back to canonical names so users don't have to know them.
        app_name = (app_name or "").strip() or "risha-judge"
        cls_name = (cls_name or "").strip() or "JudgeQwen32B"
        intent = (intent or "").strip()
        if not intent:
            err = {"error": "empty intent — RishaModalJudge needs the intent_prompt"}
            return (json.dumps(err, indent=2), 0.0, "regen", "")

        try:
            image_b64 = self._encode_png_b64(image)
        except Exception as e:
            err = {"error": "failed to encode IMAGE tensor", "detail": str(e)}
            return (json.dumps(err, indent=2), 0.0, "regen", "")

        # Cross-app Modal call. Cold-starts the judge container on first hit
        # (~90-150 s for vLLM); warm calls are 1-6 s depending on model size.
        t0 = time.time()
        try:
            JudgeCls = modal.Cls.from_name(app_name, cls_name)
            judge_obj = JudgeCls()
            result: dict[str, Any] = await judge_obj.judge.remote.aio(
                image_b64, intent, int(attempt))
        except Exception as e:
            err = {"error": "modal judge call failed", "detail": str(e),
                   "app_name": app_name, "cls": cls_name}
            return (json.dumps(err, indent=2), 0.0, "regen", "")
        dt = time.time() - t0

        # Strip internal underscore keys for the readable JSON, but keep them
        # in a separate "_meta" block so the user can audit if curious.
        meta = {k: v for k, v in result.items() if k.startswith("_")}
        clean = {k: v for k, v in result.items() if not k.startswith("_")}
        clean["_judge_meta"] = {
            "cls":           cls_name,
            "wall_seconds":  round(dt, 2),
            "attempt":       attempt,
            "raw_chars":     len(meta.get("_raw", "")),
        }

        score        = float(clean.get("score", 0.0) or 0.0)
        verdict      = str(clean.get("verdict", "regen") or "regen")
        next_prompt  = str(clean.get("next_prompt", "") or "")
        verdict_json = json.dumps(clean, indent=2, ensure_ascii=False)

        return (verdict_json, score, verdict, next_prompt)


NODE_CLASS_MAPPINGS = {"RishaModalJudge": RishaModalJudge}
NODE_DISPLAY_NAME_MAPPINGS = {"RishaModalJudge": "Risha · Modal Judge"}
