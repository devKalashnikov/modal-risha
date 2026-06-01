"""Modal app: ComfyUI for Risha visual pipeline.

Deploy:
    modal deploy modal_comfyui.py

After deploy, Modal prints a URL like:
    https://<workspace>--risha-comfyui-ui.modal.run

Point the comfyui-mcp at that URL by setting COMFYUI_HOST in Claude's mcp.json:
    "comfyui": {
      "command": "npx",
      "args": ["-y", "comfyui-mcp"],
      "env": {
        "COMFYUI_HOST": "<workspace>--risha-comfyui-ui.modal.run",
        "COMFYUI_PORT": "443",
        "COMFYUI_PROTOCOL": "https",
        "CIVITAI_API_TOKEN": "...",
        "HUGGINGFACE_TOKEN": "..."
      }
    }

GPU choice:
- L40S        — 48GB, ~$2/hr. Fits Flux.2 9B, Qwen-Image fp8, Wan 14B fp8. Default.
- B200        — 180GB, ~$6/hr. For Wan VACE 14B bf16 + multi-condition stacks.
- H100        — 80GB, ~$4/hr. Sweet spot for Wan 2.2 + SCAIL.
- RTX-Pro-6000 — 96GB Blackwell. Modal exposes via gpu="RTX-PRO-6000".

Switch GPU via the GPU constant below.
"""

import subprocess
import modal

# ── Configuration ────────────────────────────────────────────────────────────
APP_NAME = "risha-comfyui"
# GPU choice:
# - L40S         — 48GB Ada, ~$2/hr. Cost-efficient default.
# - H100         — 80GB Hopper, ~$4/hr. 2-3x L40S on bf16/fp16 diffusion.
#                   Best availability for benchmark runs (B200 pool is capacity-queued).
# - B200         — 180GB Blackwell, ~$6/hr. Fastest Modal offers for T2I (~16s/gen
#                   on Qwen-2512) but capacity-queued as of 2026-04-23.
# Changed B200 → H100 2026-04-23 after observing capacity queueing.
# Changed H100 → A100-80GB 2026-04-24 after H100 pool started queuing again.
# Changed A100-80GB → fallback list 2026-04-30 — user wants compute speed, not
# just VRAM. Modal picks the first available from this list:
#   B200      — Blackwell, ~5-6 it/s on Flux.2 fp8 (4x A100 throughput, ~$6/hr)
#   H100      — Hopper,    ~3-4 it/s (2-3x A100, ~$4/hr)
#   A100-80GB — Ampere,    ~1-1.5 it/s (safety net, ~$3/hr)
# All three have ≥80GB VRAM, fits Flux.2 35GB + judge dependencies.
GPU = ["B200", "H100", "A100-80GB", "L40S"]
# COMFY_VERSION pin removed — must be a tag supporting Qwen-Edit-2511's
# `index_timestep_zero` (i.e. ≥ 2025-12-22). Letting comfy-cli grab latest at
# build time; the Modal image is baked once per deploy, so reproducibility is
# per-deploy. To re-pin, set COMFY_VERSION and add `--version` back to the
# install command below.
PYTHON_VERSION = "3.11"

# PyTorch — pin to current stable (2.11.0, released 2026-03-23 — VERIFIED as
# the latest across GitHub releases, PyPI, and both cu126/cu128 wheel indexes.
# 2.11 ships FlashAttention-4 backend for Hopper + Blackwell GPUs).
# Higher than this would be nightly (2.12.dev) — explicitly avoided per scope.
TORCH_VERSION = "2.11.0"
# CUDA 12.8 toolkit — newer than cu126, better Tensor Core utilization on
# Blackwell, backward-compatible to Ada Lovelace (L40S) and Hopper (H100).
# Same PyTorch version, but newer CUDA = the actual optimization lever
# available within "stable" constraints.
CUDA_WHEEL = "cu128"

# Persistent volumes — survive across container restarts
models_vol = modal.Volume.from_name("risha-comfy-models", create_if_missing=True)
output_vol = modal.Volume.from_name("risha-comfy-outputs", create_if_missing=True)
custom_nodes_vol = modal.Volume.from_name("risha-comfy-custom-nodes", create_if_missing=True)

# ── Image build ──────────────────────────────────────────────────────────────
# Base image installs ComfyUI via comfy-cli (Comfy Org's official installer).
# Custom nodes are installed at build time so they're baked into the image and
# don't need to re-download on every cold start.
image = (
    modal.Image.debian_slim(python_version=PYTHON_VERSION)
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0", "wget")
    .pip_install(
        "comfy-cli==1.7.2",
        "huggingface_hub[hf_transfer]==0.26.2",
        "fastapi[standard]==0.115.0",
        # modal SDK so RishaModalJudge custom node can call the deployed
        # risha-judge app via cross-app `modal.Cls.from_name(...).remote()`.
        "modal>=0.66.0",
    )
    .run_commands(
        # Install ComfyUI itself (latest tag — see COMFY_VERSION note above)
        "yes | comfy --skip-prompt install --fast-deps --nvidia",
    )
    .run_commands(
        # Pin PyTorch to current stable so comfy-cli's bundled torch doesn't
        # drift on future builds. Re-installs over comfy-cli's torch (idempotent).
        f"pip install --upgrade --force-reinstall torch=={TORCH_VERSION} torchvision torchaudio "
        f"--index-url https://download.pytorch.org/whl/{CUDA_WHEEL}",
    )
    .run_commands(
        # ─── Phase 1 MVP custom nodes (eval-loop minimum) ────────────────────
        # Registry-available packs via comfy-cli.
        "comfy node install comfyui_controlnet_aux",  # DWPose / depth / canny
        "comfy node install rgthree-comfy",           # Display Any + switches
        "comfy node install ComfyUI-KJNodes",          # utility + image stitch
        "comfy node install ComfyUI-Manager",          # runtime dep for some packs
        "comfy node install ComfyUI-ImageReward",      # secondary numeric scorer
    )
    .run_commands(
        # 1038lab/ComfyUI-QwenVL — offline VLM judge fallback. Not in Registry.
        "git clone --depth=1 https://github.com/1038lab/ComfyUI-QwenVL "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-QwenVL",
        "pip install -r /root/comfy/ComfyUI/custom_nodes/ComfyUI-QwenVL/requirements.txt",
        # tkreuziger/comfyui-claude — PRIMARY judge + scripting. Anthropic API
        # nodes (DescribeImage for vision-judge, TransformText for re-prompting).
        # Requires ANTHROPIC_API_KEY secret (read from env at runtime).
        "git clone --depth=1 https://github.com/tkreuziger/comfyui-claude "
        "/root/comfy/ComfyUI/custom_nodes/comfyui-claude",
        "pip install anthropic>=0.40.0",
        # ─── Phase 2+ packs intentionally deferred from MVP ──────────────────
    )
    .run_commands(
        # Clear Volume mount targets — Modal can't mount on non-empty paths.
        # comfy-cli's install populates these dirs; nuking + recreating empty
        # lets the persistent Volumes mount cleanly on container start.
        "rm -rf /root/comfy/ComfyUI/models /root/comfy/ComfyUI/output /root/comfy/ComfyUI/custom_nodes_extra",
        "mkdir -p /root/comfy/ComfyUI/models /root/comfy/ComfyUI/output /root/comfy/ComfyUI/custom_nodes_extra",
    )
    # Bake the explicit model-path map so ComfyUI scans the mounted Volume's
    # subfolders even if defaults change in a future ComfyUI release.
    .add_local_file(
        "extra_model_paths.yaml",
        "/root/comfy/ComfyUI/extra_model_paths.yaml",
        copy=True,
    )
    # Bake the Risha custom node pack into the image. Edits require a redeploy.
    .add_local_dir(
        "custom_nodes/risha_nodes",
        "/root/comfy/ComfyUI/custom_nodes/risha_nodes",
        copy=True,
    )
    # Bake rubrics + plans + workflows into /root/comfy/ComfyUI/input/ so the
    # RishaRubricLoader, RishaCSVRowLoader, and RishaPlanExecutor find them at
    # their default paths. Edits to rubrics are hot-reloaded (IS_CHANGED=time_ns);
    # workflow + CSV edits need a redeploy OR an upload-through-UI.
    .add_local_dir(
        "orchestrator/rubrics",
        "/root/comfy/ComfyUI/input/rubrics",
        copy=True,
    )
    .add_local_dir(
        "workflows",
        "/root/comfy/ComfyUI/input/workflows",
        copy=True,
    )
    # Bake the current CSV plan as latest.csv. /input/ is NOT a mounted volume,
    # so an image rebuild is the only way to ship a CSV into the container.
    # Re-bake (redeploy) whenever the plan changes.
    .add_local_file(
        "user-uploads/qareeb_keyframes.csv",
        "/root/comfy/ComfyUI/input/plans/latest.csv",
        copy=True,
    )
    .env(
        {
            "HF_HUB_ENABLE_HF_TRANSFER": "1",  # 5x faster HF downloads
            "PYTHONUNBUFFERED": "1",
        }
    )
)

app = modal.App(APP_NAME, image=image)


# ── Web server: interactive ComfyUI UI ───────────────────────────────────────
@app.function(
    gpu=GPU,
    volumes={
        "/root/comfy/ComfyUI/models": models_vol,
        "/root/comfy/ComfyUI/output": output_vol,
        "/root/comfy/ComfyUI/custom_nodes_extra": custom_nodes_vol,
    },
    secrets=[
        # ANTHROPIC_API_KEY — read by tkreuziger/comfyui-claude nodes for
        # Claude API calls (vision judge + prompt rewrite + edit directives).
        # Create with: modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...
        modal.Secret.from_name("anthropic", required_keys=["ANTHROPIC_API_KEY"]),
    ],
    max_containers=1,  # single shared dev instance
    scaledown_window=3600,  # keep warm 1 hr after last request — long enough for an interactive iteration session, manually kill via `modal app stop risha-comfyui` when done
    timeout=3600,  # 1 hour per session
)
@modal.concurrent(max_inputs=10)
@modal.web_server(port=8188, startup_timeout=180)
def ui():
    """Launch ComfyUI as an interactive web server."""
    subprocess.Popen(
        "comfy launch -- --listen 0.0.0.0 --port 8188",
        shell=True,
    )


# ── API: queue workflows from outside (for the comfyui-mcp) ──────────────────
# The @web_server above already exposes the full ComfyUI HTTP API on the same
# URL (POST /prompt, GET /history, WebSocket /ws). The comfyui-mcp talks to
# those endpoints directly. No separate API class needed.


# ── Non-interactive: run the plan end-to-end in one container ─────────────────
# Usage:
#   modal run modal_comfyui.py::run_plan_test \
#       --start-row 0 --end-row 8 --skip-static-slides
#
# Starts ComfyUI as a subprocess inside the container, waits for it to be
# ready, then POSTs each matching CSV row's workflow to /prompt. Polls /queue
# until drained, commits the output volume, and exits. No browser required.
@app.function(
    gpu=GPU,
    volumes={
        "/root/comfy/ComfyUI/models": models_vol,
        "/root/comfy/ComfyUI/output": output_vol,
        "/root/comfy/ComfyUI/custom_nodes_extra": custom_nodes_vol,
    },
    secrets=[
        modal.Secret.from_name("anthropic", required_keys=["ANTHROPIC_API_KEY"]),
    ],
    timeout=5400,  # 90 min — enough for 8 frames + judge on H100 cold start
)
def run_plan_test(
    start_row: int = 0,
    end_row: int = 8,
    skip_static_slides: bool = True,
    csv_path: str = "/root/comfy/ComfyUI/input/plans/latest.csv",
    flux2_workflow: str = "/root/comfy/ComfyUI/input/workflows/_archive/risha_keyframe_pipeline__flux2.json",
    ernie_workflow: str = "/root/comfy/ComfyUI/input/workflows/_archive/risha_keyframe_pipeline__ernie.json",
    server_url: str = "http://127.0.0.1:8188",
):
    """Drive the 30-second test end-to-end without opening the UI.

    Reads the CSV, for each row in [start_row..end_row] picks the right
    workflow (flux2 vs ernie) based on `generator_model`, patches the
    RishaCSVRowLoader's row_index, POSTs to /prompt, then polls /queue until
    the runtime is idle. Commits the output volume at the end.
    """
    import csv as _csv
    import json
    import os
    import subprocess as _sp
    import time
    from pathlib import Path
    from urllib import request as urlrequest
    from urllib.error import URLError

    # 1. Launch ComfyUI in the background. Leave stdout + stderr unredirected
    # so Modal's log stream captures ComfyUI progress + errors.
    proc = _sp.Popen(
        "comfy launch -- --listen 127.0.0.1 --port 8188",
        shell=True,
    )

    # 2. Wait for /system_stats to respond (model scan can take ~60s cold).
    ready_by = time.time() + 300
    while time.time() < ready_by:
        try:
            with urlrequest.urlopen(f"{server_url}/system_stats", timeout=5) as r:
                if r.status == 200:
                    break
        except (URLError, ConnectionError, TimeoutError):
            pass
        if proc.poll() is not None:
            raise RuntimeError(
                f"comfy launch exited early (code={proc.returncode}); "
                "check preceding Modal log lines for the cause"
            )
        time.sleep(3)
    else:
        raise RuntimeError("ComfyUI did not become ready within 5 min")

    print(f"[run_plan_test] ComfyUI ready. start_row={start_row} end_row={end_row}")

    # 3. Load workflows + CSV.
    if not Path(csv_path).exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    flux2_wf = json.loads(Path(flux2_workflow).read_text(encoding="utf-8"))
    ernie_wf = json.loads(Path(ernie_workflow).read_text(encoding="utf-8"))
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.DictReader(f))

    end_row = min(end_row, len(rows) - 1)
    client_id = f"risha-plan-{int(time.time())}"

    def _patch(wf: dict, i: int, kf_id: str) -> dict:
        """Strip `_comment` at every level, patch RishaCSVRowLoader to this
        row, give every prompt a unique seed + filename prefix so frames are
        distinguishable on disk and in the latent space."""
        patched = json.loads(json.dumps(wf))
        unique_seed = (i + 1) * 1000 + 7  # deterministic but different per row
        clean = {}
        for node_id, node in patched.items():
            if not isinstance(node, dict):
                continue  # top-level _comment string
            node_clean = {k: v for k, v in node.items() if k != "_comment"}
            ins = node_clean.get("inputs")
            if isinstance(ins, dict):
                ins = {k: v for k, v in ins.items() if k != "_comment"}
                ct = node_clean.get("class_type")
                if ct == "RishaCSVRowLoader":
                    ins["csv_path"] = csv_path
                    ins["row_index"] = i
                elif ct == "RandomNoise" and "noise_seed" in ins:
                    ins["noise_seed"] = unique_seed
                elif ct == "KSampler" and "seed" in ins:
                    ins["seed"] = unique_seed
                elif ct == "SaveImage" and "filename_prefix" in ins:
                    base = str(ins["filename_prefix"]).rstrip("/")
                    ins["filename_prefix"] = f"{base}_{kf_id}"
                node_clean["inputs"] = ins
            clean[node_id] = node_clean
        return clean

    def _post(wf: dict) -> dict:
        body = json.dumps({"prompt": wf, "client_id": client_id}).encode("utf-8")
        req = urlrequest.Request(
            f"{server_url}/prompt",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    queued, skipped = [], []
    for i in range(start_row, end_row + 1):
        row = rows[i]
        kf = row.get("keyframe_id", f"row{i}")
        motion = (row.get("motion_intent") or "").strip().lower()
        model = (row.get("generator_model") or "").strip().lower()

        if skip_static_slides and motion == "static_slide":
            skipped.append(f"{kf} static_slide")
            continue

        if model.startswith("flux2"):
            wf = _patch(flux2_wf, i, kf)
        elif model.startswith("ernie"):
            wf = _patch(ernie_wf, i, kf)
        else:
            skipped.append(f"{kf} unknown_model={model or '-'}")
            continue

        try:
            resp = _post(wf)
            queued.append((kf, resp.get("prompt_id", "?"), model))
            print(f"[run_plan_test] QUEUED {kf:8} model={model:12} pid={resp.get('prompt_id')}")
        except URLError as e:
            print(f"[run_plan_test] ERROR {kf}: {e}")
        time.sleep(0.2)

    # 4. Poll /queue until drained; also check /history for per-frame status.
    print(f"[run_plan_test] queued={len(queued)} skipped={len(skipped)}; polling...")
    stall_by = time.time() + 5400
    last_report = 0
    reported_done = set()
    pid_to_kf = {pid: kf for kf, pid, _ in queued}
    while time.time() < stall_by:
        try:
            with urlrequest.urlopen(f"{server_url}/queue", timeout=10) as r:
                q = json.loads(r.read().decode("utf-8"))
        except (URLError, ConnectionError, TimeoutError) as e:
            print(f"[run_plan_test] /queue poll error: {e}")
            time.sleep(10)
            continue
        running = len(q.get("queue_running", []))
        pending = len(q.get("queue_pending", []))
        total = running + pending
        now = time.time()

        # Report per-frame completion or error via /history.
        try:
            with urlrequest.urlopen(f"{server_url}/history", timeout=15) as r:
                hist = json.loads(r.read().decode("utf-8"))
            for pid, kf in pid_to_kf.items():
                if pid in reported_done or pid not in hist:
                    continue
                entry = hist[pid]
                status = entry.get("status", {}) or {}
                completed = status.get("completed", False)
                status_str = status.get("status_str", "")
                msgs = status.get("messages", []) or []
                note = ""
                for m in msgs:
                    if isinstance(m, list) and len(m) >= 2 and m[0] == "execution_error":
                        err = m[1] or {}
                        note = f" ERROR node={err.get('node_type')} ex={err.get('exception_message','')[:200]}"
                        break
                tag = "DONE" if completed and not note else ("ERROR" if note else status_str.upper())
                print(f"[run_plan_test] {tag:6} {kf:8} pid={pid[:8]}{note}")
                reported_done.add(pid)
        except (URLError, ConnectionError, TimeoutError, KeyError, ValueError) as e:
            if now - last_report > 60:
                print(f"[run_plan_test] /history poll error: {e}")

        if now - last_report > 30:
            print(f"[run_plan_test] queue running={running} pending={pending} completed={len(reported_done)}/{len(queued)}")
            last_report = now
        if total == 0:
            print("[run_plan_test] queue drained.")
            break
        time.sleep(5)
    else:
        print("[run_plan_test] TIMEOUT waiting for queue to drain")

    # 5. Flush outputs to the persistent volume.
    output_vol.commit()

    # 6. Summarize what landed.
    out_dir = Path("/root/comfy/ComfyUI/output/risha_plan")
    images = sorted(p.name for p in out_dir.glob("*.png")) if out_dir.exists() else []
    attempts = out_dir / "plan_attempts.json"
    log_preview = ""
    if attempts.exists():
        try:
            log_preview = attempts.read_text(encoding="utf-8")[-2000:]
        except Exception:
            pass

    print("=" * 60)
    print(f"queued:  {len(queued)}")
    for kf, pid, m in queued:
        print(f"  {kf:8} {m:12} {pid}")
    print(f"skipped: {len(skipped)}")
    for s in skipped:
        print(f"  {s}")
    print(f"images in output/risha_plan: {len(images)}")
    for img in images[-20:]:
        print(f"  {img}")
    if log_preview:
        print(f"--- plan_attempts.json (tail) ---\n{log_preview}")
    print("=" * 60)

    return {
        "queued": [kf for kf, _, _ in queued],
        "skipped": skipped,
        "image_count": len(images),
    }


# ── Optional: model pre-download function ────────────────────────────────────
# Run once after first deploy to populate the volume:
#   modal run modal_comfyui.py::download_models
@app.function(
    volumes={"/root/comfy/ComfyUI/models": models_vol},
    timeout=3600,
    secrets=[modal.Secret.from_name("huggingface", required_keys=["HF_TOKEN"])],
)
def download_models():
    """Pre-populate the models volume with the Phase 1 MVP model set.

    All HF filenames verified 2026-04-23 against live `/api/models/.../tree`
    JSON (not WebFetch — that fails on JS-rendered tree pages).

    MVP scope: Qwen-Image-2512 + Qwen-Edit-2511 + Qwen3-VL-8B-Thinking judge.
    Klein 4B is deferred (no official non-gated TE source — see task #21).
    Flux.1 Dev / ERNIE / 9B variants kept commented for benchmark expansion.
    """
    from huggingface_hub import hf_hub_download, snapshot_download
    import os

    HF_TOKEN = os.environ["HF_TOKEN"]

    DM = "/root/comfy/ComfyUI/models/diffusion_models"
    TE = "/root/comfy/ComfyUI/models/text_encoders"
    VAE = "/root/comfy/ComfyUI/models/vae"
    LORAS = "/root/comfy/ComfyUI/models/loras"
    LLM = "/root/comfy/ComfyUI/models/LLM/Qwen-VL"  # 1038lab QwenVL pack scan path

    # ════════════════════════════════════════════════════════════════════════
    # MVP STACK (required for first end-to-end run): Qwen + judge
    # ════════════════════════════════════════════════════════════════════════

    # ── Qwen-Image-2512 (PRODUCTION T2I, Apache-2.0) — BF16 only ──────────────
    # 40.9 GB. fp8_e4m3fn variant dropped per user direction (2026-04-23);
    # quality ceiling > fast-iter tradeoff on L40S/RTX-PRO-6000.
    hf_hub_download(
        repo_id="Comfy-Org/Qwen-Image_ComfyUI",
        filename="split_files/diffusion_models/qwen_image_2512_bf16.safetensors",
        local_dir=DM, token=HF_TOKEN,
    )
    # Qwen2.5-VL-7B text encoder (NOT qwen_3 — Qwen-Image uses Qwen2.5-VL)
    hf_hub_download(
        repo_id="Comfy-Org/Qwen-Image_ComfyUI",
        filename="split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        local_dir=TE, token=HF_TOKEN,
    )
    # Qwen-Image VAE (shared between Qwen-Image-2512 and Qwen-Edit-2511)
    hf_hub_download(
        repo_id="Comfy-Org/Qwen-Image_ComfyUI",
        filename="split_files/vae/qwen_image_vae.safetensors",
        local_dir=VAE, token=HF_TOKEN,
    )

    # ── Qwen-Image-Edit-2511 (PRODUCTION multi-ref, Apache-2.0) — BF16 only ───
    # 40.9 GB. fp8mixed variant dropped per user direction (2026-04-23).
    hf_hub_download(
        repo_id="Comfy-Org/Qwen-Image-Edit_ComfyUI",
        filename="split_files/diffusion_models/qwen_image_edit_2511_bf16.safetensors",
        local_dir=DM, token=HF_TOKEN,
    )

    # ── Lightning LoRAs (4-step + 8-step for negatives) ───────────────────────
    # 2512 + 2511 specific files — old Qwen-Image-Lightning silently degrades.
    for fn in (
        "Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors",
        "Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors",
    ):
        hf_hub_download(repo_id="lightx2v/Qwen-Image-2512-Lightning",
                        filename=fn, local_dir=LORAS, token=HF_TOKEN)
    for fn in (
        "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
        "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors",
    ):
        hf_hub_download(repo_id="lightx2v/Qwen-Image-Edit-2511-Lightning",
                        filename=fn, local_dir=LORAS, token=HF_TOKEN)

    # ── VLM judge: Qwen3-VL-32B-Thinking-FP8 (Apache-2.0, ~32 GB) ────────────
    # User direction 2026-04-23: judge must be comparable to Sonnet quality.
    # "Qwen 3.5-VL" doesn't exist as an official release — Alibaba's latest VL
    # line is Qwen3-VL, with 32B the largest dense variant. FP8 fits alongside
    # Flux.2 Dev fp8mixed (35 GB) on H100 80 GB when judge is invoked with
    # keep_model_loaded toggled between stages (generator unloads before judge
    # loads and vice-versa). 8B-Thinking retained as fallback path.
    snapshot_download(
        repo_id="Qwen/Qwen3-VL-32B-Thinking-FP8",
        local_dir=f"{LLM}/Qwen3-VL-32B-Thinking-FP8",
        token=HF_TOKEN,
        allow_patterns=["*.safetensors", "*.json", "*.txt"],
    )
    # 8B-Thinking kept as lightweight fallback for fast-iteration smoke tests.
    snapshot_download(
        repo_id="Qwen/Qwen3-VL-8B-Thinking",
        local_dir=f"{LLM}/Qwen3-VL-8B-Thinking",
        token=HF_TOKEN,
        allow_patterns=["*.safetensors", "*.json", "*.txt"],
    )

    # 8B-Instruct — PRODUCTION judge variant. Thinking models refuse strict
    # JSON output contracts (they write meta-prose about the task instead).
    # Instruct is RLHF-trained for format adherence. ~16 GB. Use this as the
    # default judge; Thinking stays on disk for free-form image description.
    snapshot_download(
        repo_id="Qwen/Qwen3-VL-8B-Instruct",
        local_dir=f"{LLM}/Qwen3-VL-8B-Instruct",
        token=HF_TOKEN,
        allow_patterns=["*.safetensors", "*.json", "*.txt"],
    )

    # Kimi-VL-A3B-Thinking-2506 — third judge variant for v1 benchmark.
    # MoE 16B total / 3B active. Apache-2.0. vLLM-supported (trust_remote_code).
    # Strong on charts/diagrams + detailed visual reasoning, ~32 GB on disk.
    # NO allow_patterns: Kimi ships its tokenizer as a binary file
    # (`tiktoken.model` / `tokenizer.model`) that doesn't match the usual
    # extension globs. Initial v1 deploy missed it → tokenizer load crashed.
    KIMI = "/root/comfy/ComfyUI/models/LLM/Kimi-VL"
    snapshot_download(
        repo_id="moonshotai/Kimi-VL-A3B-Thinking-2506",
        local_dir=f"{KIMI}/Kimi-VL-A3B-Thinking-2506",
        token=HF_TOKEN,
    )

    # ════════════════════════════════════════════════════════════════════════
    # BENCHMARK EXPANSION — Flux.2 Dev + ERNIE (user direction 2026-04-23)
    # ════════════════════════════════════════════════════════════════════════

    # ── Flux.2 Dev (benchmark + Stage 3 editor, paid BFL license acceptable) ─
    # UNET (35 GB fp8mixed already on volume from pre-MVP downloads).
    hf_hub_download(
        repo_id="Comfy-Org/flux2-dev",
        filename="split_files/diffusion_models/flux2_dev_fp8mixed.safetensors",
        local_dir=DM, token=HF_TOKEN,
    )
    # Mistral-3-small TE (fp8 — 17 GB, fits alongside everything on L40S 48GB).
    # bf16 (33 GB) and fp4_mixed (11 GB) available if needed later.
    hf_hub_download(
        repo_id="Comfy-Org/flux2-dev",
        filename="split_files/text_encoders/mistral_3_small_flux2_fp8.safetensors",
        local_dir=TE, token=HF_TOKEN,
    )
    # Flux.2 VAE (336 MB — shared with ERNIE per Flux2 architecture family).
    hf_hub_download(
        repo_id="Comfy-Org/flux2-dev",
        filename="split_files/vae/flux2-vae.safetensors",
        local_dir=VAE, token=HF_TOKEN,
    )
    # Flux.2 Turbo LoRA — ByteZSzn's community version referenced by
    # Comfy-Org/workflow_templates/image_flux2_text_to_image.json (canonical).
    # 2.76 GB. Drops Flux.2 Dev from 20 steps → 8 steps @ cfg 1.
    hf_hub_download(
        repo_id="ByteZSzn/Flux.2-Turbo-ComfyUI",
        filename="Flux_2-Turbo-LoRA_comfyui.safetensors",
        local_dir=LORAS, token=HF_TOKEN,
    )

    # ── ERNIE-Image (benchmark + Flux2-arch alt path, Apache-2.0) ────────────
    # ernie-image.safetensors (16 GB) already on volume. Pull remaining deps.
    hf_hub_download(
        repo_id="Comfy-Org/ERNIE-Image",
        filename="diffusion_models/ernie-image-turbo.safetensors",
        local_dir=DM, token=HF_TOKEN,
    )
    # ministral-3-3b — main TE for ERNIE (not to be confused with Flux.2's
    # mistral_3_small_flux2 above — different architecture + tokenizer).
    hf_hub_download(
        repo_id="Comfy-Org/ERNIE-Image",
        filename="text_encoders/ministral-3-3b.safetensors",
        local_dir=TE, token=HF_TOKEN,
    )
    # Optional prompt enhancer LLM — Chinese-system-prompt prompt rewriter.
    # We'll bypass this in our workflows (Claude does prompt engineering
    # upstream), but pulling it lets us A/B later if desired.
    hf_hub_download(
        repo_id="Comfy-Org/ERNIE-Image",
        filename="text_encoders/ernie-image-prompt-enhancer.safetensors",
        local_dir=TE, token=HF_TOKEN,
    )

    # ════════════════════════════════════════════════════════════════════════
    # DEFERRED (uncomment per-batch for benchmark expansion)
    # ════════════════════════════════════════════════════════════════════════
    # Flux.1 Dev (BFL non-commercial, R&D ceiling only):
    # clip_l + t5xxl_fp16 + ae.safetensors + flux1-dev already on volume.
    # Add Hyper-FLUX-8steps LoRA if benchmarking:
    # hf_hub_download(repo_id="nakodanei/Hyper-FLUX.1-dev-8steps-lora-fp16",
    #     filename="Hyper-FLUX.1-dev-8steps-lora-fp16.safetensors", local_dir=LORAS)
    #
    # Flux.2 Klein 4B/9B — DEFERRED (gated BFL repo, see task #21).

    # ── Modal volumes don't auto-commit. Persist before exit. ─────────────────
    models_vol.commit()

    print("Models downloaded:")
    print(f"  Qwen-Image-2512        bf16                → {DM}")
    print(f"  Qwen-Image-Edit-2511   bf16                → {DM}")
    print(f"  Flux.2 Dev             fp8mixed            → {DM}")
    print(f"  ERNIE-Image + Turbo    bf16                → {DM}")
    print(f"  Qwen2.5-VL-7B TE       fp8_scaled          → {TE}")
    print(f"  Mistral-3-small (F2)   fp8                 → {TE}")
    print(f"  Ministral-3-3b (ERNIE)                     → {TE}")
    print(f"  ERNIE prompt-enhancer                      → {TE}")
    print(f"  Qwen-Image VAE + Flux2 VAE                 → {VAE}")
    print(f"  Lightning LoRAs        4+8 step × 2 models → {LORAS}")
    print(f"  Flux.2 Turbo LoRA      2.76 GB             → {LORAS}")
    print(f"  Qwen3-VL-32B-Thinking-FP8  judge primary   → {LLM}/Qwen3-VL-32B-Thinking-FP8")
    print(f"  Qwen3-VL-8B-Thinking       judge fallback  → {LLM}/Qwen3-VL-8B-Thinking")
    print("Volume committed.")


@app.local_entrypoint()
def main():
    """Default `modal run` action — print the live UI URL."""
    print(f"App: {APP_NAME}")
    print(f"GPU: {GPU}")
    print("Deploy with:  modal deploy modal_comfyui.py")
    print("Then visit the UI URL Modal printed and queue your first workflow.")
