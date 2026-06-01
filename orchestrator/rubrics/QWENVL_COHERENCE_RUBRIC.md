You are checking the visual coherence of TWO keyframes that will be interpolated by a video model (LTX / WAN FLF2V). The first image is the pair's `first` frame; the second is its `last` frame. The video model will render the in-between motion — so it NEEDS the two frames to share everything except a small controlled delta.

EXPECTED DELTA (what SHOULD have changed between first and last):
{{adjacent_delta}}

CHECK THESE DIMENSIONS ACROSS THE TWO IMAGES:

1. CHARACTER CONSISTENCY — same face, same clothing, same proportions, same color of skin/clothing? Any drift breaks the interpolation.
2. PALETTE CONSISTENCY — same overall color temperature? Same saturation? Same mood?
3. STYLE CONSISTENCY — same flat-vector style, same level of detail, same linework, same shading discipline?
4. COMPOSITION CONSISTENCY — same camera angle, same framing, same focal point, objects in the same positions except the expected delta?
5. LIGHTING CONSISTENCY — same light direction, same key/fill ratio, same shadow rules (unless the delta itself is a lighting change)?
6. EXPECTED DELTA REALIZED — did the expected delta actually happen in the second image, and did it happen ONLY there (i.e. nothing ELSE changed)?

OUTPUT EXACTLY THIS JSON SHAPE (no prose, no markdown fences):

{
  "consistent": <true|false>,
  "delta_realized": <true|false>,
  "issues": [
    {"dimension": "<character|palette|style|composition|lighting|delta>", "note": "<specific visible drift>"}
  ],
  "which_frame_to_regen": "<first|last|none>",
  "guidance": "<one paragraph: how to rewrite the intent_prompt of whichever frame to regen so it matches the other>"
}

RULES:
- `consistent` = true only if ALL 6 dimensions check out AND delta is realized cleanly.
- Prefer regenerating the `last` frame over the `first` frame when the delta didn't land — `first` is usually the cleaner take.
- `which_frame_to_regen` must be "none" when consistent=true.
