"""Modal app: WAN 2.2 First-Last-Frame video generation for Risha.

Sibling to `modal_comfyui.py`. Kept as a SEPARATE Modal app because:
  * Video models are huge (~37 GB on disk) — isolating them keeps the T2I app's
    cold start lean and lets us pick a beefier GPU tier (H100/B200) per-app.
  * Independent redeploys: video-only changes don't bounce the T2I instance
    that the comfyui-mcp is already pointed at.
  * Separate URL → easier for teammates to test one pipeline without touching
    the other.

Deploy:
    modal deploy modal_video.py

After deploy Modal prints a URL like:
    https://<workspace>--risha-video-ui.modal.run

First-deploy bootstrap (one-time, ~25-40 min over fast peering):
    modal run modal_video.py::download_models

Headless smoke test (uses the bundled wan_flf_test workflow):
    modal run modal_video.py::run_flf_test

Workflow ships under /input/workflows/wan_flf_test.api.json — the WAN 2.2
official-fp8 dual hi-lo two-pass with Lightning LoRAs (4 steps total, uni_pc,
beta, ModelSamplingSD3 shift=5). Drop a start frame + end frame via the UI,
queue, get an MP4 back. See wan-flf-video skill docs for the math.
"""

import subprocess
import modal

# ── Configuration ────────────────────────────────────────────────────────────
APP_NAME = "risha-video"

# GPU choice — WAN 2.2 i2v 14B fp8 dual hi-lo loading needs ~28 GB peak VRAM
# during the Hi pass (the Lo UNET is offloaded between passes). L40S 48 GB is
# the floor; H100/B200 give meaningfully faster sampling. Same fallback list
# as modal_comfyui.py so deploys land on whatever the pool has available.
GPU = ["B200", "H100", "A100-80GB", "L40S"]

PYTHON_VERSION = "3.11"
# PyTorch 2.11 / cu128 — same pin as modal_comfyui.py so both apps share the
# same wheel cache and ABI. See modal_comfyui.py:58 for the rationale.
TORCH_VERSION = "2.11.0"
CUDA_WHEEL = "cu128"

# Persistent volumes — own namespace so video weights don't pollute the
# T2I/Edit volume. Cheaper to wipe + re-download if a model variant changes.
models_vol = modal.Volume.from_name("risha-video-models", create_if_missing=True)
output_vol = modal.Volume.from_name("risha-video-outputs", create_if_missing=True)
custom_nodes_vol = modal.Volume.from_name("risha-video-custom-nodes", create_if_missing=True)

# ── Image build ──────────────────────────────────────────────────────────────
# Same comfy-cli base as modal_comfyui.py. Adds VideoHelperSuite (VHS_*) and
# keeps KJNodes (ImageResizeKJv2) + rgthree (Lora Loader Stack). The Risha
# custom node pack is baked in too so the motion engine's CSV / plan loaders
# are available if we wire FLF into the orchestrator later.
image = (
    modal.Image.debian_slim(python_version=PYTHON_VERSION)
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0", "wget")
    .pip_install(
        "comfy-cli==1.7.2",
        "huggingface_hub[hf_transfer]==0.26.2",
        "fastapi[standard]==0.115.0",
        "modal>=0.66.0",
    )
    .run_commands(
        "yes | comfy --skip-prompt install --fast-deps --nvidia",
    )
    .run_commands(
        f"pip install --upgrade --force-reinstall torch=={TORCH_VERSION} torchvision torchaudio "
        f"--index-url https://download.pytorch.org/whl/{CUDA_WHEEL}",
    )
    .run_commands(
        # Custom nodes needed for WAN 2.2 FLF:
        #   - ComfyUI-VideoHelperSuite → VHS_VideoCombine (MP4 encode)
        #   - ComfyUI-KJNodes          → ImageResizeKJv2 (crop+pad to 16-aligned dims)
        #   - rgthree-comfy            → Lora Loader Stack (LoRA stacking)
        #   - ComfyUI-Manager          → runtime dep some packs install against
        "comfy node install comfyui-videohelpersuite",
        "comfy node install ComfyUI-KJNodes",
        "comfy node install rgthree-comfy",
        "comfy node install ComfyUI-Manager",
    )
    .run_commands(
        # Same scrub as modal_comfyui.py — let the persistent Volumes mount
        # cleanly on /models /output /custom_nodes_extra without conflicting
        # with comfy-cli's seeded directories.
        "rm -rf /root/comfy/ComfyUI/models /root/comfy/ComfyUI/output /root/comfy/ComfyUI/custom_nodes_extra",
        "mkdir -p /root/comfy/ComfyUI/models /root/comfy/ComfyUI/output /root/comfy/ComfyUI/custom_nodes_extra",
    )
    .add_local_file(
        "extra_model_paths.yaml",
        "/root/comfy/ComfyUI/extra_model_paths.yaml",
        copy=True,
    )
    # Risha node pack — gives the video app access to RishaCSVRowLoader,
    # motion selector, etc. Not strictly required by wan_flf_test.api.json
    # but cheap to ship and enables future plan-driven FLF runs.
    .add_local_dir(
        "custom_nodes/risha_nodes",
        "/root/comfy/ComfyUI/custom_nodes/risha_nodes",
        copy=True,
    )
    # Bake workflows into /input/workflows so the UI's "Load" button finds
    # them and the headless run_flf_test can read them off disk.
    .add_local_dir(
        "workflows",
        "/root/comfy/ComfyUI/input/workflows",
        copy=True,
    )
    .env(
        {
            "HF_HUB_ENABLE_HF_TRANSFER": "1",
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
    max_containers=1,
    scaledown_window=3600,
    timeout=3600,
)
@modal.concurrent(max_inputs=10)
@modal.web_server(port=8188, startup_timeout=240)
def ui():
    """Launch ComfyUI as an interactive web server.

    Startup_timeout is bumped to 240s (vs 180 on the T2I app) because the model
    scan picks up ~30 GB of WAN weights on cold start, which takes longer to
    enumerate than the Qwen stack.
    """
    subprocess.Popen(
        "comfy launch -- --listen 0.0.0.0 --port 8188",
        shell=True,
    )


# ── Headless: smoke test the FLF workflow end-to-end ─────────────────────────
@app.function(
    gpu=GPU,
    volumes={
        "/root/comfy/ComfyUI/models": models_vol,
        "/root/comfy/ComfyUI/output": output_vol,
        "/root/comfy/ComfyUI/custom_nodes_extra": custom_nodes_vol,
    },
    timeout=3600,
)
def run_flf_test(
    workflow_path: str = "/root/comfy/ComfyUI/input/workflows/wan_flf_test.api.json",
    start_image: str = "start.png",
    end_image: str = "end.png",
    positive: str = "smooth seamless transition, gentle camera drift, illustrative animation",
    seed: int = 42,
    server_url: str = "http://127.0.0.1:8188",
):
    """Drive a single WAN FLF render headlessly.

    Pre-req: upload `start.png` and `end.png` to the input dir via the UI's
    file uploader (or POST /upload/image). This function patches the workflow
    to reference those filenames, queues, and waits for the MP4.
    """
    import json
    import subprocess as _sp
    import time
    from pathlib import Path
    from urllib import request as urlrequest
    from urllib.error import URLError

    proc = _sp.Popen(
        "comfy launch -- --listen 127.0.0.1 --port 8188",
        shell=True,
    )

    # Wait for /system_stats — model scan on cold start can take ~90s with
    # ~30 GB of video weights on the volume.
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
                "check Modal log lines above for the cause"
            )
        time.sleep(3)
    else:
        raise RuntimeError("ComfyUI did not become ready within 5 min")

    print(f"[run_flf_test] ComfyUI ready. workflow={workflow_path}")

    if not Path(workflow_path).exists():
        raise FileNotFoundError(f"Workflow not found: {workflow_path}")
    wf = json.loads(Path(workflow_path).read_text(encoding="utf-8"))

    # Patch: swap image filenames + positive prompt + seed across all
    # KSampler nodes. Defensive against renumbering — match by class_type.
    for node_id, node in wf.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        ins = node.get("inputs", {})
        if ct == "LoadImage":
            title = (node.get("_meta", {}) or {}).get("title", "").lower()
            if "start" in title:
                ins["image"] = start_image
            elif "end" in title:
                ins["image"] = end_image
        elif ct == "CLIPTextEncode":
            title = (node.get("_meta", {}) or {}).get("title", "").lower()
            if "positive" in title:
                ins["text"] = positive
        elif ct == "KSamplerAdvanced" and "noise_seed" in ins:
            ins["noise_seed"] = seed

    body = json.dumps(
        {"prompt": wf, "client_id": f"risha-flf-{int(time.time())}"}
    ).encode("utf-8")
    req = urlrequest.Request(
        f"{server_url}/prompt",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode("utf-8"))
    prompt_id = resp.get("prompt_id", "?")
    print(f"[run_flf_test] QUEUED prompt_id={prompt_id}")

    # Poll /queue until drained (FLF on H100 ~ 120-180s for 81 frames @ 4
    # steps; on L40S ~ 280-420s. Generous timeout.)
    stall_by = time.time() + 1800
    last_report = 0
    while time.time() < stall_by:
        try:
            with urlrequest.urlopen(f"{server_url}/queue", timeout=10) as r:
                q = json.loads(r.read().decode("utf-8"))
        except (URLError, ConnectionError, TimeoutError) as e:
            print(f"[run_flf_test] /queue poll error: {e}")
            time.sleep(5)
            continue
        total = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        now = time.time()
        if now - last_report > 20:
            print(f"[run_flf_test] queue total={total}")
            last_report = now
        if total == 0:
            print("[run_flf_test] queue drained.")
            break
        time.sleep(5)
    else:
        print("[run_flf_test] TIMEOUT waiting for queue to drain")

    output_vol.commit()

    # Summarize what landed.
    out_dir = Path("/root/comfy/ComfyUI/output")
    videos = sorted(p.name for p in out_dir.rglob("*.mp4"))
    print("=" * 60)
    print(f"prompt_id: {prompt_id}")
    print(f"mp4 outputs ({len(videos)}):")
    for v in videos[-10:]:
        print(f"  {v}")
    print("=" * 60)
    return {"prompt_id": prompt_id, "video_count": len(videos)}


# ── Model downloads — run ONCE per fresh volume ──────────────────────────────
# Usage:
#   modal run modal_video.py::download_models
#
# Pulls ~37 GB total. Resumable: hf_hub_download skips files already cached on
# the volume, so re-running this is cheap.
@app.function(
    volumes={"/root/comfy/ComfyUI/models": models_vol},
    timeout=3600,
    secrets=[modal.Secret.from_name("huggingface", required_keys=["HF_TOKEN"])],
)
def download_models():
    """Populate the video models volume with the WAN 2.2 FLF stack.

    Verified against live HF repo trees 2026-06-02:
      * Comfy-Org/Wan_2.2_ComfyUI_Repackaged   (i2v 14B fp8 + umt5 + vae)
      * Comfy-Org/Wan_2.1_ComfyUI_repackaged   (clip_vision_h)         ← note: lowercase 'r'
      * lightx2v/Wan2.2-Lightning              (4-step i2v hi+lo LoRAs)
    """
    from huggingface_hub import hf_hub_download
    import os

    HF_TOKEN = os.environ["HF_TOKEN"]

    DM = "/root/comfy/ComfyUI/models/diffusion_models"
    TE = "/root/comfy/ComfyUI/models/text_encoders"
    VAE = "/root/comfy/ComfyUI/models/vae"
    CV = "/root/comfy/ComfyUI/models/clip_vision"
    LORAS = "/root/comfy/ComfyUI/models/loras"

    # ── WAN 2.2 i2v 14B Hi + Lo (fp8 scaled) — 14.3 GB each ─────────────────
    # Two-pass dual-noise architecture is mandatory for 2.2 (see wan-flf-video
    # skill docs). Hi handles steps 0→N/2 (structure), Lo handles N/2→N (detail).
    for fn in (
        "split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    ):
        hf_hub_download(
            repo_id="Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
            filename=fn, local_dir=DM, token=HF_TOKEN,
        )

    # ── UMT5-XXL text encoder (fp8, 6.74 GB) ────────────────────────────────
    hf_hub_download(
        repo_id="Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        filename="split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        local_dir=TE, token=HF_TOKEN,
    )

    # ── WAN 2.1 VAE (254 MB) — shared by 2.1 + 2.2 i2v pipelines ────────────
    hf_hub_download(
        repo_id="Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        filename="split_files/vae/wan_2.1_vae.safetensors",
        local_dir=VAE, token=HF_TOKEN,
    )

    # ── CLIP-Vision-H (1.26 GB) — lives in the 2.1 repackaged repo only ─────
    # Note lowercase 'r' in "repackaged" — different repo, same author.
    hf_hub_download(
        repo_id="Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        filename="split_files/clip_vision/clip_vision_h.safetensors",
        local_dir=CV, token=HF_TOKEN,
    )

    # ── Lightning LoRAs (4-step) — paired Hi + Lo, ~600 MB each ─────────────
    # Files inside the Seko-V1 dir are named generically (high_noise_model /
    # low_noise_model). The download_models step renames them on the volume
    # so the workflow can pick them by a unique filename — generic names would
    # collide with future Seko-V2 etc.
    for variant in ("high_noise_model", "low_noise_model"):
        local_path = hf_hub_download(
            repo_id="lightx2v/Wan2.2-Lightning",
            filename=f"Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/{variant}.safetensors",
            local_dir=LORAS, token=HF_TOKEN,
        )
        # hf_hub_download preserves the subfolder → flatten + rename.
        target = os.path.join(LORAS, f"wan22_i2v_lightning_4step_seko_v1_{variant}.safetensors")
        if not os.path.exists(target):
            os.rename(local_path, target)

    models_vol.commit()

    print("Video models downloaded:")
    print(f"  WAN 2.2 i2v Hi/Lo  fp8        → {DM}")
    print(f"  UMT5-XXL TE        fp8        → {TE}")
    print(f"  WAN 2.1 VAE                   → {VAE}")
    print(f"  CLIP-Vision-H                 → {CV}")
    print(f"  Lightning LoRAs    4-step x2  → {LORAS}")
    print("Volume committed.")


@app.local_entrypoint()
def main():
    """Default `modal run` action — print where to go next."""
    print(f"App: {APP_NAME}")
    print(f"GPU: {GPU}")
    print()
    print("Bootstrap (one-time):  modal run modal_video.py::download_models")
    print("Deploy UI:             modal deploy modal_video.py")
    print("Headless FLF test:     modal run modal_video.py::run_flf_test")
