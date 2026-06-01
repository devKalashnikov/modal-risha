"""Risha custom nodes for ComfyUI — keyframe pipeline primitives.

Every node is a pure primitive that composes with standard ComfyUI graph
semantics. No network I/O except RishaPlanExecutor (which only talks to the
local ComfyUI HTTP API). Heavy VLM work is done by the 1038lab/ComfyUI-QwenVL
pack — these nodes only build the prompts that QwenVL consumes and parse the
text it emits.
"""

from .csv_row_loader import RishaCSVRowLoader, RishaCSVRowCount
from .rubric_loader import RishaRubricLoader, RishaRubricRender
from .judge_parser import RishaJudgeParser
from .prompt_rewriter import RishaPromptRewriterPrompt, RishaRewriterParser
from .pair_coherence import RishaPairCoherencePrompt, RishaPairCoherenceParser
from .attempt_logger import RishaAttemptLogger
from .plan_executor import RishaPlanExecutor
from .model_router import RishaModelRouter
from .string_utils import RishaStringConcat, RishaStringFormat, RishaStringToFile
from .modal_judge_caller import RishaModalJudge


NODE_CLASS_MAPPINGS = {
    "RishaCSVRowLoader": RishaCSVRowLoader,
    "RishaCSVRowCount": RishaCSVRowCount,
    "RishaRubricLoader": RishaRubricLoader,
    "RishaRubricRender": RishaRubricRender,
    "RishaJudgeParser": RishaJudgeParser,
    "RishaPromptRewriterPrompt": RishaPromptRewriterPrompt,
    "RishaRewriterParser": RishaRewriterParser,
    "RishaPairCoherencePrompt": RishaPairCoherencePrompt,
    "RishaPairCoherenceParser": RishaPairCoherenceParser,
    "RishaAttemptLogger": RishaAttemptLogger,
    "RishaPlanExecutor": RishaPlanExecutor,
    "RishaModelRouter": RishaModelRouter,
    "RishaStringConcat": RishaStringConcat,
    "RishaStringFormat": RishaStringFormat,
    "RishaStringToFile": RishaStringToFile,
    "RishaModalJudge": RishaModalJudge,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RishaCSVRowLoader": "Risha · CSV Row Loader",
    "RishaCSVRowCount": "Risha · CSV Row Count",
    "RishaRubricLoader": "Risha · Rubric Loader",
    "RishaRubricRender": "Risha · Rubric Render (fill template)",
    "RishaJudgeParser": "Risha · Judge Parser",
    "RishaPromptRewriterPrompt": "Risha · Rewriter Prompt Builder",
    "RishaRewriterParser": "Risha · Rewriter Parser",
    "RishaPairCoherencePrompt": "Risha · Pair Coherence Prompt",
    "RishaPairCoherenceParser": "Risha · Pair Coherence Parser",
    "RishaAttemptLogger": "Risha · Attempt Logger",
    "RishaPlanExecutor": "Risha · Plan Executor",
    "RishaModelRouter": "Risha · Model Router",
    "RishaStringConcat": "Risha · String Concat",
    "RishaStringFormat": "Risha · String Format",
    "RishaStringToFile": "Risha · String To File",
    "RishaModalJudge": "Risha · Modal Judge",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
