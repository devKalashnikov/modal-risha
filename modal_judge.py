"""Modal app: VLM judge for Risha keyframe pipeline.

Three judge variants, each its own @app.cls so weights stay resident:
  JudgeQwen8B    — Qwen3-VL-8B-Instruct, ~16 GB, fastest, RLHF format-following
  JudgeQwen32B   — Qwen3-VL-32B-Thinking-FP8, ~32 GB, deepest spatial reasoning
  JudgeKimi      — Kimi-VL-A3B-Thinking-2506, MoE 16B/3B-active, strong on charts

All use vLLM with `guided_decoding` (json schema from Pydantic) so output JSON
is structurally enforced — eliminates the "Thinking model writes prose instead
of JSON" failure mode we hit with the in-Comfy 1038lab judge node.

Image bytes are passed in directly (not path) — avoids Modal volume-sync race
between ComfyUI's writes and the judge's reads. Orchestrator fetches PNG from
ComfyUI /view, base64-encodes, sends to judge.

Deploy:
    modal deploy modal_judge.py

Call from orchestrator:
    Judge = modal.Cls.from_name("risha-judge", "JudgeQwen8B")
    judge = Judge()
    verdict = judge.judge.remote(image_b64=..., intent="...", attempt=1)
"""

import modal

APP_NAME = "risha-judge"

# Reuse the same models volume so judge weights live next to Flux.2 weights.
models_vol = modal.Volume.from_name("risha-comfy-models", create_if_missing=False)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install(
        # Pin pair: vllm 0.11.0 calls tokenizer.all_special_tokens_extended which
        # transformers 4.57+ dropped from slow tokenizers (Qwen3-VL ships only
        # a slow Qwen2Tokenizer in its repo). Three options:
        #  (a) bump vllm to 0.11.1+ which patched get_cached_tokenizer
        #  (b) pin transformers to 4.56.x (but then qwen3_vl arch missing)
        #  (c) force fast tokenizer (Qwen3-VL doesn't ship one)
        # Picking (a) — latest stable vllm at our pin date.
        "vllm==0.11.1",
        "transformers==4.57.1",
        "blobfile",  # Kimi-VL tokenizer dep
        "pillow==10.4.0",
        "pydantic>=2.0",
        "xgrammar>=0.1.11",
        "huggingface_hub[hf_transfer]>=0.32.0",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "PYTHONUNBUFFERED": "1"})
)

app = modal.App(APP_NAME, image=image)


# ── Shared rubric + schema ───────────────────────────────────────────────────
RUBRIC = """You are an image-quality judge for an animated explainer-video pipeline.

You receive ONE generated keyframe image plus the INTENT (what the image was
supposed to depict). Decide whether the image satisfies the intent.

Three possible verdicts:
- "pass"   — image is good enough to ship, all critical intent elements present.
- "edit"   — image is mostly right but has a SURGICAL fix needed: object swap,
             attribute change, removal/addition of a small element. Composition,
             lighting, and overall style must STAY. Use this when a partial-
             denoise edit can plausibly fix it. route="edit".
- "regen"  — image misses the intent at a structural level: wrong composition,
             wrong setting, missing major subject, wrong lighting/time of day,
             wrong number of subjects. Needs a fresh T2I pass with a new prompt.
             route="t2i".

Score 0-10:
  10 = textbook execution
  8-9 = ship it, minor nitpicks only
  6-7 = needs an edit, recoverable
  4-5 = regen needed
  0-3 = unusable

For "edit": next_prompt MUST be an instructional sentence describing ONLY what
to change ("Replace the modern wheel with a wooden tiller. Keep everything
else identical.") — diff-style, not a re-description of the whole scene.

For "regen": next_prompt is the FULL replacement prose prompt for the T2I model.

For "pass": next_prompt is null and route is "done".

Cultural correctness:
When the INTENT specifies culturally-loaded terms — region, dress, architecture,
props, period, palette, or social norms — apply your world knowledge to verify
the image actually depicts what those words mean. The orchestrator writes
prompts that name SWANA-specific elements; your job is to check the image
honoured them, not just that a generic equivalent is present.

Reference vocabulary (non-exhaustive; treat each as a concrete visual referent):
  - Dress: kandura, dishdasha, thobe, abaya, ghutra, agal, hijab, niqab,
    djellaba, jalabiya, kaftan, keffiyeh, shalwar kameez, sirwal
  - Architecture: mashrabiya, arabesque arches, mudbrick walls, riad,
    qubba, mihrab, sandstone souk, palm-frond roofing, wind tower (barjeel)
  - Setting / region: medina, souk, oasis, wadi, dhow, kasbah, Khaleej,
    Maghreb, Levant, Najd, Hejaz

Failure cases to flag in `issues` and route accordingly:
  - intent names a specific garment / prop / motif and the image shows a
    generic-Western or wrong-region equivalent (fedora instead of ghutra,
    gothic arch instead of arabesque, European market instead of souk)
      → verdict="edit"  if a single element is wrong but the locale reads right
      → verdict="regen" if the whole setting / population / era reads wrong
  - anachronism within the named period (e.g. modern car in a 19th-c. medina)
      → verdict="edit" or "regen" by severity
  - cultural detail named in intent is simply absent (intent says "kandura",
    image shows a man in a t-shirt) → at minimum verdict="edit"

If the INTENT does NOT name cultural detail, do NOT invent a cultural
expectation — score on what was actually asked.

Output ONLY the JSON. No prose, no <think> tags, no fences.
"""


def schema_dict():
    """Pydantic schema enforced by vLLM guided_decoding."""
    from pydantic import BaseModel, Field
    from typing import Literal, Optional

    class Verdict(BaseModel):
        score: float = Field(..., ge=0, le=10)
        verdict: Literal["pass", "edit", "regen"]
        route: Literal["edit", "t2i", "done"]
        issues: list[str]
        next_prompt: Optional[str]

    return Verdict


def downscale_b64(image_b64: str) -> str:
    """Decode base64 PNG, downscale to cap visual tokens, re-encode as base64
    PNG. 1664×928 native → ~7.8k visual tokens; capping to ~896 longest side →
    ~2k tokens, fits 8k context comfortably."""
    import base64
    import io
    from PIL import Image
    raw = base64.b64decode(image_b64)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if max(img.size) > 896:
        img.thumbnail((896, 896))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_messages(rubric: str, intent: str, attempt: int, image_b64: str):
    """vLLM 0.11.x OpenAI-compatible chat format. image_url accepts either a
    URL or a data:image/...;base64,... data URL.

    Cache-bust marker: vllm-0.11.1-image-url-format-v2
    """
    data_url = f"data:image/png;base64,{image_b64}"
    return [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {
                    "type": "text",
                    "text": (
                        f"{rubric}\n\nINTENT_PROMPT:\n{intent}\n\n"
                        f"ATTEMPT_NUMBER: {attempt}\n\n"
                        "Emit the JSON now."
                    ),
                },
            ],
        }
    ]


def run_inference(llm, image_b64: str, intent: str, attempt: int, max_tokens: int = 600) -> dict:
    """vLLM 0.11.x renamed `guided_decoding` → `structured_outputs` (now a
    StructuredOutputsParams object, not a dict). Old kwarg still works but emits
    a DeprecationWarning AND a typed-attribute error inside the v1 engine path.
    """
    from vllm import SamplingParams
    from vllm.sampling_params import StructuredOutputsParams
    small_b64 = downscale_b64(image_b64)
    Verdict = schema_dict()
    sampling = SamplingParams(
        temperature=0.0,
        max_tokens=max_tokens,
        structured_outputs=StructuredOutputsParams(json=Verdict.model_json_schema()),
    )
    msgs = build_messages(RUBRIC, intent, attempt, small_b64)
    out = llm.chat(msgs, sampling_params=sampling)
    text = out[0].outputs[0].text
    finish_reason = getattr(out[0].outputs[0], "finish_reason", "unknown")
    try:
        parsed = Verdict.model_validate_json(text).model_dump()
    except Exception as e:
        # Thinking model can blow the token budget mid-JSON (esp. on busy
        # images with many issues). Surface it as a regen verdict instead of
        # 500'ing the worker so the caller sees something usable.
        parsed = {
            "score": 0.0,
            "verdict": "regen",
            "route": "t2i",
            "issues": [f"judge output unparseable ({type(e).__name__}): {str(e)[:200]}",
                       f"finish_reason={finish_reason}"],
            "next_prompt": None,
            "_parse_error": True,
        }
    parsed["_raw"] = text
    parsed["_finish_reason"] = finish_reason
    parsed["_attempt"] = attempt
    return parsed


# ── Judge class ──────────────────────────────────────────────────────────────
# Single judge: Qwen3-VL-32B-Thinking-FP8.
# - 8B-Instruct dropped after v1 bench: warm latency identical (~3-5 s) but
#   verdicts noticeably less articulate than 32B-Thinking on the same images.
# - Kimi-VL dropped: MoonVit vision tower OOMs at profile time on A100-80GB
#   even with mm_processor_kwargs and enforce_eager. Not worth the troubleshoot.
@app.cls(
    gpu="A100-80GB",
    volumes={"/models": models_vol},
    scaledown_window=3600,  # 1 hr warm window — match comfy
    timeout=900,
)
class JudgeQwen32B:
    @modal.enter()
    def load(self):
        from vllm import LLM
        self.llm = LLM(
            model="/models/LLM/Qwen-VL/Qwen3-VL-32B-Thinking-FP8",
            max_model_len=8192,
            gpu_memory_utilization=0.85,
            enforce_eager=False,
            quantization="fp8",
            limit_mm_per_prompt={"image": 1},
        )

    @modal.method()
    def judge(self, image_b64: str, intent: str, attempt: int = 1) -> dict:
        # Thinking variant emits hidden reasoning before the JSON. 2000 was
        # tight — a complex image (busy scene, lots of issues to enumerate)
        # truncated mid-string at col 7917. 4000 gives headroom.
        return run_inference(self.llm, image_b64, intent, attempt, max_tokens=4000)


# ── Smoke test ────────────────────────────────────────────────────────────────
@app.local_entrypoint()
def smoke(local_image: str = "test_outputs_30s/k002_flux2.png",
          intent: str = "An explainer keyframe of three oil derricks on a Gulf coastal plain at sunset, flat illustrative style."):
    """Quick check: modal run modal_judge.py::smoke"""
    import base64
    import json
    from pathlib import Path

    image_b64 = base64.b64encode(Path(local_image).read_bytes()).decode("ascii")
    judge = JudgeQwen32B()
    result = judge.judge.remote(image_b64, intent, 1)
    print(json.dumps({k: v for k, v in result.items() if k != "_raw"}, indent=2))
    print("--- raw ---")
    print(result.get("_raw", ""))
