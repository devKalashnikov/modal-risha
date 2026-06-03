# modal-risha

Three Modal apps that make up the visual backend for Risha:

- **`risha-comfyui`** — ComfyUI server for keyframe generation (Qwen-Image-2512 T2I + Qwen-Edit-2511). Exposes the standard ComfyUI HTTP API.
- **`risha-judge`** — Qwen3-VL-32B-Thinking-FP8 judge on vLLM. Scores a generated image against an intent prompt, returns a structured verdict JSON.
- **`risha-video`** — ComfyUI server for video generation. Ships WAN 2.2 i2v FLF (dual hi-lo two-pass with Lightning LoRAs) and LTX-2.3 22B distilled fp8 FLF workflows.

This repo contains the **minimum source needed to deploy all three apps** into the shared Risha Modal workspace. Models, secrets, and volumes already exist in that workspace — no re-downloads or re-creates needed.

---

## Prerequisites

- Python 3.10+
- A Modal account that has been added to the shared Risha workspace

---

## One-time setup

### 1. Clone

```bash
git clone https://github.com/devKalashnikov/modal-risha.git
cd modal-risha
```

### 2. Install the Modal CLI

```bash
pip install modal
```

### 3. Authenticate Modal

```bash
modal token new
```

Opens a browser. **Make sure you pick the shared Risha workspace** (not your personal one) during the flow. Verify afterwards:

```bash
modal profile list     # asterisk marks the active profile
```

If you ever need to switch:

```bash
modal profile activate <workspace-name>
```

---

## Deploying the apps

```bash
modal deploy modal_judge.py        # ~3-5 min first time, ~30 s subsequent
modal deploy modal_comfyui.py      # ~5-10 min first time
modal deploy modal_video.py        # ~5-10 min first time
```

Order doesn't matter — the apps are independent.

All three apps reference shared, already-populated Modal volumes and shared Modal secrets (`anthropic`, `huggingface`). You do **not** need to re-download models or re-create secrets — the bootstrap was run once on the workspace.

- `risha-comfyui` → `risha-comfy-models` (~120 GB)
- `risha-video` → `risha-video-models` (~76 GB: WAN 2.2 stack + LTX-2.3 stack)

The `workflows/` directory ships:
- `motion_kenburns.api.json`, `motion_static_slide.api.json` — motion-engine API workflows used by the `RishaMotionSelector` + `RishaKenBurnsRender` + `RishaStaticHoldRender` node chain on `risha-comfyui`.
- `wan_flf_test.api.json` — WAN 2.2 first-last-frame test on `risha-video` (headless-compatible).
- `video_ltx2_3_flf2v.json` — LTX-2.3 22B distilled fp8 FLF on `risha-video` (graph form, load via the UI).

T2I and Edit workflows are still being tuned and live outside the repo.

After a successful deploy, Modal prints the live URLs of the form:
- `https://<workspace>--risha-comfyui-ui.modal.run`
- `https://<workspace>--risha-video-ui.modal.run`

---

## Managing running apps

### Three "stop" levels — pick by what you actually want

**Bounce the warm container (URL stays alive)**
```bash
modal app list                          # find your apps and their state
modal container list                    # see container IDs
modal container stop <container-id>     # kill one specific container
```
Use when the UI is hung, GPU stuck, or you want to free the GPU slot without redeploying. Next request cold-starts a new container.

**Fully stop the deployment (URL goes 404)**
```bash
modal app stop risha-video              # or risha-comfyui / risha-judge
```
Use when you're done testing for the day or want to redeploy clean. `modal deploy modal_video.py` brings it back.

**Tail logs / debug**
```bash
modal app logs risha-video              # live tail
```

### Tuning sleep / scaledown / parallelism

These live in the `@app.function(...)` decorator at the top of each `ui()` function (see `modal_video.py:124-134` and `modal_comfyui.py` for the equivalent block):

```python
@app.function(
    gpu=GPU,
    volumes={...},
    max_containers=1,
    scaledown_window=3600,   # idle seconds before container shuts down (default: 1 hour)
    timeout=3600,            # hard ceiling on a single request
)
```

| Knob | What it does | When to change |
|---|---|---|
| `scaledown_window` | How long the container stays warm after the last request. | Drop to `300` (5 min) for short-burst usage to stop bleeding GPU-seconds. Push to `7200` (2 hr) while actively iterating. |
| `max_containers` | Cap on parallel GPU instances. | Bump to `2-3` if multiple people need to render at once. Leave at `1` for predictable cost. |
| `timeout` | Hard wall-clock ceiling for a single function call. | Only matters if a job hangs. Render times: T2I ~15-30 s, video ~120-420 s. `3600` is generous; leave it. |

Edit the value, then `modal deploy <app>.py`. The change takes effect on the next cold start (or after the current container scales down).

### See what's costing money

```bash
modal app stats                         # per-app GPU-seconds + invocation count
modal volume list                       # all volumes
modal volume ls risha-video-models      # confirm models landed
```

`modal app stats` is the first thing to check if a bill spikes — it splits by app + function so you can tell whether the warm-pool window or a long render run was the heavy hitter.
