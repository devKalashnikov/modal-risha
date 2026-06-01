# modal-risha

Two Modal apps that make up the visual backend for Risha:

- **`risha-comfyui`** — ComfyUI server. Exposes the standard ComfyUI HTTP API for queuing keyframe generation workflows.
- **`risha-judge`** — Qwen3-VL-32B-Thinking-FP8 hosted on vLLM. Scores a generated image against an intent prompt, returns a structured verdict JSON.

This repo contains the **minimum source needed to deploy both apps** into the shared Risha Modal workspace. Models, secrets, and volumes already exist in that workspace — no re-downloads or re-creates needed.

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

## Deploying the two apps

```bash
modal deploy modal_judge.py        # ~3-5 min first time, ~30 s subsequent
modal deploy modal_comfyui.py      # ~5-10 min first time
```

Order doesn't matter — the apps are independent.

> **Heads up:** `modal_comfyui.py` currently **will not deploy successfully** because the `workflows/` directory is empty in this repo. The finalized workflow JSON(s) will be pushed here once they're locked in. `modal_judge.py` deploys fine today.

Both apps reference shared, already-populated Modal volumes (~120 GB of model weights live in `risha-comfy-models`) and shared Modal secrets (`anthropic`, `huggingface`). You do **not** need to re-download models or re-create secrets.

After a successful deploy, Modal prints the live ComfyUI URL of the form `https://<workspace>--risha-comfyui-ui.modal.run`.
