# modal-risha

Two Modal apps that make up the visual backend for Risha:

- **`risha-comfyui`** — ComfyUI server. Exposes the standard ComfyUI HTTP API for queuing keyframe generation workflows.
- **`risha-judge`** — Qwen3-VL-32B-Thinking-FP8 hosted on vLLM. Scores a generated image against an intent prompt, returns a structured verdict JSON.

This repo contains the **minimum source needed to deploy both apps** into the shared Risha Modal workspace. Models, secrets, and volumes already exist in that workspace — no re-downloads or re-creates needed.

---

## Prerequisites

- Python 3.10+
- A Modal account that has been added to the shared Risha workspace
- Read access to this private GitHub repo (Dawood adds you as a collaborator)

---

## One-time setup

### 1. Clone

```bash
git clone https://github.com/devKalashnikov/modal-risha.git
cd modal-risha
```

Because the repo is private, plain `git clone` over HTTPS only works once your GitHub account is authenticated locally. Easiest path:

```bash
# install the GitHub CLI, then:
gh auth login          # pick GitHub.com -> HTTPS -> web browser -> paste the one-time code
```

After that, all clones / pulls / pushes Just Work without prompts. Alternatives are a personal access token or an SSH key — see <https://docs.github.com/en/authentication> if you prefer either.

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

## Deploying the two apps

```bash
modal deploy modal_judge.py        # ~3-5 min first time, ~30 s subsequent
modal deploy modal_comfyui.py      # ~5-10 min first time
```

Order doesn't matter — the apps are independent.

> **Heads up:** `modal_comfyui.py` currently **will not deploy successfully** because the `workflows/` directory is empty in this repo. The finalized workflow JSON(s) will be pushed here once they're locked in. `modal_judge.py` deploys fine today.

Both apps reference shared, already-populated Modal volumes (~120 GB of model weights live in `risha-comfy-models`) and shared Modal secrets (`anthropic`, `huggingface`). You do **not** need to re-download models or re-create secrets.

After a successful deploy, Modal prints the live ComfyUI URL of the form `https://<workspace>--risha-comfyui-ui.modal.run`. Save it; that is the base URL you POST workflows to.

---

## Calling the apps from your code

### ComfyUI — standard HTTP

```python
import requests, uuid, time

COMFY = "https://<workspace>--risha-comfyui-ui.modal.run"

# 1. queue a workflow (workflow_json is the API-format dict)
r = requests.post(
    f"{COMFY}/prompt",
    json={"prompt": workflow_json, "client_id": str(uuid.uuid4())},
    timeout=30,
)
prompt_id = r.json()["prompt_id"]

# 2. poll until done
while True:
    hist = requests.get(f"{COMFY}/history/{prompt_id}", timeout=15).json()
    if prompt_id in hist:
        break
    time.sleep(2)

# 3. fetch the rendered PNG bytes from the SaveImage output
png_bytes = requests.get(
    f"{COMFY}/view",
    params={"filename": filename, "subfolder": subfolder, "type": "output"},
    timeout=30,
).content
```

### Judge — Modal cross-app RPC

```python
import base64, modal

JudgeCls = modal.Cls.from_name("risha-judge", "JudgeQwen32B")
judge = JudgeCls()

verdict = judge.judge.remote(
    image_b64=base64.b64encode(png_bytes).decode("ascii"),
    intent="An explainer keyframe of three oil derricks at sunset, flat illustrative style",
    attempt=1,
)

# verdict shape:
# {
#   "score":   float,            # 0-10
#   "verdict": "pass"|"edit"|"regen",
#   "route":   "done"|"edit"|"t2i",
#   "issues":  [str, ...],
#   "next_prompt": str | None,   # edit instruction OR full T2I prompt OR None
#   "_raw":  "...",              # raw model text (diagnostic)
#   "_finish_reason": "stop" | "length",
#   "_attempt": int,
#   "_parse_error": bool,        # only present if JSON parse failed
# }
```

Always check `_parse_error` before trusting `verdict` in retry-counting logic.

---

## Day-to-day commands

```bash
modal app list                       # what's currently deployed
modal app logs risha-comfyui         # tail Comfy logs (quiet => idle)
modal app logs risha-judge           # tail judge logs
modal app stop risha-comfyui         # force scaledown -- stops paying for warm GPU
modal app stop risha-judge
modal volume ls risha-comfy-outputs  # browse rendered PNGs in the outputs volume
```

Both apps default to `scaledown_window=3600` — they stay warm for one hour after the last request, then scale to zero (no GPU cost while cold). Each request resets the timer. To change the window, edit the `scaledown_window=...` value on the `@app.cls(...)` / `@app.function(...)` decorator and redeploy.

Cold start adds roughly 60–90 s on Comfy (model scan) and 30–60 s on the judge (vLLM weight load).

---

## What's in this repo

| Path | Purpose |
|---|---|
| `modal_comfyui.py` | `risha-comfyui` app definition |
| `modal_judge.py` | `risha-judge` app definition |
| `extra_model_paths.yaml` | ComfyUI model-path map (baked into the image at build time) |
| `custom_nodes/risha_nodes/` | Custom Risha ComfyUI node pack (baked in) |
| `orchestrator/rubrics/` | Markdown rubrics consumed by the custom nodes (baked in) |
| `user-uploads/qareeb_keyframes.csv` | Plan CSV (baked in) |
| `workflows/` | Empty pending finalization — Comfy deploy will fail until populated |
| `.gitignore` | Strict allowlist that keeps everything else out of the repo |

Anything else in your local working copy is intentionally not tracked — outputs, caches, the broader project tree, etc.

---

## Questions

Ping Dawood.
