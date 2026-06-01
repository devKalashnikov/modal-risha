"""Model router — maps a `generator_model` string to a numeric branch index
the downstream rgthree/Impact switch nodes can read."""

from __future__ import annotations


MODEL_INDEX = {
    "flux2_dev": 1,
    "flux2_dev_hq": 1,  # same branch, different workflow params
    "ernie": 2,
    "ernie_turbo": 2,
    # qwen variants kept for reference but intentionally inactive
    "qwen2512": 3,
    "qwen_edit_2511": 3,
}


class RishaModelRouter:
    """Convert `generator_model` string into a branch index for Any-Switch nodes.

    Outputs:
        branch_index  — 1-based integer (1=flux2, 2=ernie, 3=qwen) for switch nodes
        is_flux2      — BOOL
        is_ernie      — BOOL
        is_qwen       — BOOL
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "generator_model": ("STRING", {"forceInput": True}),
                "default_branch": ("INT", {"default": 1, "min": 1, "max": 9}),
            },
        }

    RETURN_TYPES = ("INT", "BOOLEAN", "BOOLEAN", "BOOLEAN")
    RETURN_NAMES = ("branch_index", "is_flux2", "is_ernie", "is_qwen")
    FUNCTION = "route"
    CATEGORY = "Risha/Plan"

    def route(self, generator_model: str, default_branch: int):
        key = (generator_model or "").strip().lower()
        idx = MODEL_INDEX.get(key, default_branch)
        return (
            idx,
            idx == 1,
            idx == 2,
            idx == 3,
        )
