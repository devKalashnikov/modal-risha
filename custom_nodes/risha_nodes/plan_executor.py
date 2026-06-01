"""Plan Executor — queues the master keyframe workflow once per CSV row by
talking to the LOCAL ComfyUI HTTP API. Lets you kick off an entire video's
worth of keyframes from a single ComfyUI graph.

The executor LOADS a pre-saved workflow (API format JSON) from disk, patches
the CSVRowLoader's `row_index` for each row, and POSTs to /prompt. It does
NOT block waiting for completion — ComfyUI's queue handles that serially."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError


def _queue(server: str, workflow: dict, client_id: str) -> dict:
    body = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
    req = urlrequest.Request(
        f"{server.rstrip('/')}/prompt",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _patch_row_index(workflow: dict, csv_path: str, row_index: int) -> dict:
    """Find the RishaCSVRowLoader node and patch its inputs."""
    patched = json.loads(json.dumps(workflow))  # deep copy
    found = False
    for node_id, node in patched.items():
        if node.get("class_type") == "RishaCSVRowLoader":
            node["inputs"]["csv_path"] = csv_path
            node["inputs"]["row_index"] = row_index
            found = True
    if not found:
        raise ValueError(
            "Master workflow has no RishaCSVRowLoader node — cannot patch row_index"
        )
    return patched


class RishaPlanExecutor:
    """Queue the master workflow once per row of a CSV plan.

    Inputs:
        csv_path — path to the plan CSV
        master_workflow_path — path to the API-format workflow JSON that contains
            a RishaCSVRowLoader node (its csv_path + row_index get patched per row)
        server_url — usually http://127.0.0.1:8188 (local Comfy)
        start_row / end_row — inclusive range; use -1 for "until end"
        model_filter — prefix match on generator_model (e.g. "flux2" matches
            flux2_dev + flux2_dev_hq; "ernie" matches ernie + ernie_turbo).
            Empty string = no filter.
        dry_run — if True, just report what would be queued without POSTing
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "csv_path": ("STRING", {"default": "/root/comfy/ComfyUI/input/plans/latest.csv"}),
                "master_workflow_path": ("STRING", {
                    "default": "/root/comfy/ComfyUI/input/workflows/risha_keyframe_pipeline.json"
                }),
                "server_url": ("STRING", {"default": "http://127.0.0.1:8188"}),
                "start_row": ("INT", {"default": 0, "min": 0, "max": 9999}),
                "end_row": ("INT", {"default": -1, "min": -1, "max": 9999}),
                "model_filter": ("STRING", {"default": ""}),
                "skip_static_slides": ("BOOLEAN", {"default": True}),
                "dry_run": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("report", "queued_count")
    FUNCTION = "run"
    CATEGORY = "Risha/Plan"
    OUTPUT_NODE = True

    def run(self, csv_path: str, master_workflow_path: str, server_url: str,
            start_row: int, end_row: int, model_filter: str,
            skip_static_slides: bool, dry_run: bool):
        csv_p = Path(csv_path)
        wf_p = Path(master_workflow_path)
        if not csv_p.exists():
            raise FileNotFoundError(f"Plan CSV not found: {csv_path}")
        if not wf_p.exists():
            raise FileNotFoundError(f"Master workflow not found: {master_workflow_path}")

        with csv_p.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if end_row < 0 or end_row >= len(rows):
            end_row = len(rows) - 1

        workflow = json.loads(wf_p.read_text(encoding="utf-8"))
        client_id = f"risha-plan-{int(time.time())}"
        mf = (model_filter or "").strip().lower()

        report_lines = [
            f"Plan: {csv_path}",
            f"Workflow: {master_workflow_path}",
            f"Rows: {len(rows)} total, queueing [{start_row}..{end_row}]",
            f"Model filter: {mf or '(none)'}",
            f"Dry-run: {dry_run}",
            "-" * 60,
        ]
        queued = 0
        for i in range(start_row, end_row + 1):
            row = rows[i]
            motion = (row.get("motion_intent") or "").strip().lower()
            kf_id = row.get("keyframe_id", f"row{i}")
            row_model = (row.get("generator_model") or "").strip().lower()
            if skip_static_slides and motion == "static_slide":
                report_lines.append(f"  [{i:3d}] {kf_id:8} SKIP (static_slide)")
                continue
            if mf and not row_model.startswith(mf):
                report_lines.append(
                    f"  [{i:3d}] {kf_id:8} SKIP (model={row_model or '-'} != {mf}*)"
                )
                continue

            if dry_run:
                report_lines.append(
                    f"  [{i:3d}] {kf_id:8} DRY motion={motion} model={row_model}"
                )
                continue

            patched = _patch_row_index(workflow, str(csv_p), i)
            try:
                resp = _queue(server_url, patched, client_id)
                pid = resp.get("prompt_id", "?")
                report_lines.append(
                    f"  [{i:3d}] {kf_id:8} QUEUED prompt_id={pid}"
                )
                queued += 1
            except URLError as e:
                report_lines.append(
                    f"  [{i:3d}] {kf_id:8} ERROR {type(e).__name__}: {e}"
                )
            # Tiny delay so ComfyUI's /prompt handler doesn't thrash under rapid bursts
            time.sleep(0.1)

        report_lines.append("-" * 60)
        report_lines.append(f"Queued: {queued}")
        return ("\n".join(report_lines), queued)
