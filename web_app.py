#!/usr/bin/env python3

import argparse
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from render_lottie import RenderCancelled, render_video


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = (
    Path("/tmp/videofy_outputs")
    if os.environ.get("RAILWAY_ENVIRONMENT") and not os.environ.get("VIDEOFY_OUTPUT_ROOT")
    else Path(os.environ.get("VIDEOFY_OUTPUT_ROOT", str(PROJECT_DIR)))
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4"}
MAX_VIDEO_DURATION_SECONDS = 60
QUALITY_PRESET = {"scale_factor": 2, "video_crf": 20, "video_preset": "medium"}
RAILWAY_QUALITY_PRESET = {
    "video_crf": 23,
    "video_preset": "veryfast",
    "video_threads": 4,
}
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
VIDEO_TYPES = {
    "ai-photo": {
        "label": "AI Photo",
        "folder_name": "AI Photo",
        "enabled": True,
        "description": "Upload 3 Before photos and 6 After photos. The template reuses the first and last After frames automatically.",
        "template_path": PROJECT_DIR / "templates_json" / "AI-PHOTO.json",
        "quality_preset": {"scale_factor": 1, "render_fps": 30, "encode_fps": 30},
        "upload_groups": [
            {
                "key": "before",
                "label": "Before Photos",
                "description": "These three photos fill the smaller Before containers in sequence.",
                "slots": [
                    {"field": "before_1", "label": "Before 1", "size": "small", "preview_ratio": "424 / 758"},
                    {"field": "before_2", "label": "Before 2", "size": "small", "preview_ratio": "496 / 888"},
                    {"field": "before_3", "label": "Before 3", "size": "small", "preview_ratio": "479 / 858"},
                ],
            },
            {
                "key": "after",
                "label": "After Photos",
                "description": "Upload six After photos. The first After photo is also reused at the start and finish of the sequence.",
                "slots": [
                    {"field": "after_1", "label": "After 1", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "after_2", "label": "After 2", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "after_3", "label": "After 3", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "after_4", "label": "After 4", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "after_5", "label": "After 5", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "after_6", "label": "After 6", "size": "large", "preview_ratio": "768 / 1344"},
                ],
            },
        ],
        "asset_bindings": {
            "0": "before_1",
            "1": "before_2",
            "2": "before_3",
            "3": "after_1",
            "4": "after_2",
            "5": "after_3",
            "6": "after_4",
            "7": "after_5",
            "8": "after_6",
        },
        "layout_rows": [
            ["before_1", "before_2", "before_3"],
            ["after_1", "after_2", "after_3", "after_4", "after_5", "after_6"],
        ],
    },
    "ai-filter": {
        "label": "AI Filter",
        "folder_name": "AI Filter",
        "enabled": True,
        "description": "Upload 3 pairs of Before and After photos for the three AI Filter groups.",
        "template_path": PROJECT_DIR / "templates_json" / "maska-worksGood.json",
        "quality_preset": {"scale_factor": 1, "render_fps": 30, "encode_fps": 30},
        "upload_groups": [
            {
                "key": "group-1",
                "label": "Group 1",
                "description": "Group 1 Before and Group 1 After.",
                "slots": [
                    {"field": "group_1_before", "label": "1 Before", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "group_1_after", "label": "1 After", "size": "large", "preview_ratio": "768 / 1344"},
                ],
            },
            {
                "key": "group-2",
                "label": "Group 2",
                "description": "Group 2 Before and Group 2 After.",
                "slots": [
                    {"field": "group_2_before", "label": "2 Before", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "group_2_after", "label": "2 After", "size": "large", "preview_ratio": "768 / 1344"},
                ],
            },
            {
                "key": "group-3",
                "label": "Group 3",
                "description": "Group 3 Before and Group 3 After.",
                "slots": [
                    {"field": "group_3_before", "label": "3 Before", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "group_3_after", "label": "3 After", "size": "large", "preview_ratio": "768 / 1344"},
                ],
            },
        ],
        "asset_bindings": {
            "0": "group_3_before",
            "1": "group_3_after",
            "2": "group_2_before",
            "3": "group_2_after",
            "4": "group_1_before",
            "5": "group_1_after",
        },
        "layout_rows": [
            [
                "group_1_before",
                "group_1_after",
                "group_2_before",
                "group_2_after",
                "group_3_before",
                "group_3_after",
            ],
        ],
    },
    "ai-video": {
        "label": "AI Video",
        "folder_name": "AI Video",
        "enabled": True,
        "description": "Upload 1 photo and 1 MP4 video for the AI Video template.",
        "upload_step_label": "3. Media",
        "template_path": PROJECT_DIR / "templates_json" / "AI-VIDEO (1).json",
        "quality_preset": {"scale_factor": 2, "video_crf": 23, "video_preset": "medium", "render_fps": 60, "encode_fps": 60},
        "upload_groups": [
            {
                "key": "media",
                "label": "Media",
                "description": "Photo overlay plus a portrait MP4 background video.",
                "slots": [
                    {"field": "photo", "label": "Photo", "size": "large", "preview_ratio": "1 / 1", "input_kind": "image"},
                    {"field": "video", "label": "Video", "size": "large", "preview_ratio": "1 / 1", "input_kind": "video"},
                ],
            },
        ],
        "asset_bindings": {
            "0": "photo",
        },
        "hidden_layer_inds": [27, 28],
        "video_overlay": {
            "field": "video",
            "mode": "full_frame",
        },
        "layout_rows": [["photo", "video"]],
    },
    "2-photos": {
        "label": "2 Photos",
        "folder_name": "2 Photos",
        "enabled": True,
        "description": "Upload 2 source photos plus 1 generated photo for the 2 Photos flow.",
        "template_path": PROJECT_DIR / "templates_json" / "2-Photo-Flow (1).json",
        "quality_preset": {"scale_factor": 1, "render_fps": 30, "encode_fps": 30},
        "upload_groups": [
            {
                "key": "photos",
                "label": "Photos",
                "description": "Two source photos plus the generated result photo.",
                "slots": [
                    {"field": "first_photo", "label": "1st Photo", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "second_photo", "label": "2nd Photo", "size": "large", "preview_ratio": "768 / 1344"},
                    {"field": "generated_photo", "label": "Generated Photo", "size": "large", "preview_ratio": "768 / 1344"},
                ],
            },
        ],
        "asset_bindings": {
            "0": "second_photo",
            "1": "first_photo",
            "3": "second_photo",
            "4": "first_photo",
            "5": "generated_photo",
        },
        "layout_rows": [["first_photo", "second_photo", "generated_photo"]],
    },
}


@dataclass
class RenderJob:
    id: str
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    error: str | None = None
    result: dict | None = None
    work_dir: Path | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_event: threading.Event = field(default_factory=threading.Event)


def public_video_types() -> dict[str, dict]:
    return {
        key: {
            "label": value["label"],
            "enabled": value["enabled"],
            "upload_step_label": value.get("upload_step_label", "3. Photos"),
            "layout_rows": value.get("layout_rows", []),
            "upload_groups": [
                {
                    "key": group["key"],
                    "slots": group["slots"],
                }
                for group in value["upload_groups"]
            ],
        }
        for key, value in VIDEO_TYPES.items()
    }


def video_type_output_dir(output_root: Path, video_type_key: str) -> Path:
    return output_root / VIDEO_TYPES[video_type_key]["folder_name"]


def normalize_video_title(raw_title: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub(" ", raw_title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(". ")
    return cleaned or "Untitled Video"


def build_output_filename(raw_title: str) -> str:
    title = normalize_video_title(raw_title)
    return title if title.lower().endswith(".mp4") else f"{title}.mp4"


def validate_upload(file_storage, input_kind: str) -> str:
    ext = Path(file_storage.filename or "").suffix.lower()
    allowed_extensions = VIDEO_EXTENSIONS if input_kind == "video" else IMAGE_EXTENSIONS
    if ext not in allowed_extensions:
        if input_kind == "video":
            raise ValueError("Only MP4 video files are supported for video slots.")
        raise ValueError("Only JPG, PNG, and WEBP files are supported.")
    return ext or ".png"


def probe_video_duration(video_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float((result.stdout or "").strip())


def serialize_job(job: RenderJob) -> dict:
    with job.lock:
        return {
            "jobId": job.id,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "error": job.error,
            "result": job.result,
        }


def update_job(job: RenderJob, **changes) -> None:
    with job.lock:
        for key, value in changes.items():
            setattr(job, key, value)


def runtime_quality_preset(video_type: dict) -> dict:
    preset = {**QUALITY_PRESET, **video_type.get("quality_preset", {})}
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        preset = {**preset, **RAILWAY_QUALITY_PRESET}
    return preset


def run_render_job(
    *,
    app: Flask,
    job: RenderJob,
    video_type_key: str,
    output_name: str,
    saved_paths: dict[str, Path],
) -> None:
    try:
        video_type = app.config["VIDEO_TYPES"][video_type_key]
        output_dir = video_type_output_dir(app.config["OUTPUT_ROOT"], video_type_key)
        quality_preset = runtime_quality_preset(video_type)
        update_job(job, status="running", progress=2, message="Starting render")
        app.logger.info("Starting render job %s for %s -> %s", job.id, video_type_key, output_dir)

        asset_image_paths = {
            asset_id: saved_paths[field_name]
            for asset_id, field_name in video_type["asset_bindings"].items()
        }

        def on_progress(progress: int, message: str) -> None:
            update_job(job, status="running", progress=max(0, min(100, progress)), message=message)

        result = render_video(
            template_path=video_type["template_path"],
            asset_image_paths=asset_image_paths,
            output_dir=output_dir,
            output_name=output_name,
            scale_factor=quality_preset["scale_factor"],
            video_crf=quality_preset["video_crf"],
            video_preset=quality_preset["video_preset"],
            render_fps=quality_preset.get("render_fps"),
            encode_fps=quality_preset.get("encode_fps"),
            video_threads=quality_preset.get("video_threads"),
            transparent_asset_ids=video_type.get("transparent_asset_ids"),
            hidden_layer_inds=video_type.get("hidden_layer_inds"),
            overlay_video_path=saved_paths.get(video_type.get("video_overlay", {}).get("field", "")),
            transparent_background=bool(video_type.get("video_overlay")),
            progress_callback=on_progress,
            should_cancel=job.cancel_event.is_set,
        )

        update_job(
            job,
            status="completed",
            progress=100,
            message="Done",
            result={
                "videoUrl": f"/outputs/{video_type_key}/{result.output_video.name}",
                "jsonUrl": f"/outputs/{video_type_key}/{result.rendered_json.name}",
                "videoName": result.output_video.name,
                "videoType": video_type_key,
                "videoTypeLabel": video_type["label"],
                "replacedAssetIds": result.replaced_asset_ids,
            },
        )
        app.logger.info(
            "Completed render job %s. video=%s json=%s exists(video)=%s exists(json)=%s timings=%s",
            job.id,
            result.output_video,
            result.rendered_json,
            result.output_video.exists(),
            result.rendered_json.exists(),
            result.stage_timings,
        )
    except RenderCancelled as error:
        update_job(
            job,
            status="cancelled",
            message="Terminated",
            error=str(error),
        )
        app.logger.warning("Cancelled render job %s: %s", job.id, error)
    except Exception as error:
        update_job(
            job,
            status="failed",
            error=str(error),
            message="Render failed",
        )
        app.logger.exception("Render job %s failed", job.id)
    finally:
        if job.work_dir and job.work_dir.exists():
            shutil.rmtree(job.work_dir, ignore_errors=True)


def create_app(output_root: Path) -> Flask:
    app = Flask(__name__, template_folder=str(PROJECT_DIR / "templates"))
    app.logger.setLevel(logging.INFO)
    app.config["OUTPUT_ROOT"] = output_root.expanduser().resolve()
    app.config["OUTPUT_ROOT"].mkdir(parents=True, exist_ok=True)
    app.config["JOBS"] = {}
    app.config["VIDEO_TYPES"] = {
        key: {
            **value,
            "template_path": value.get("template_path", Path()).expanduser().resolve()
            if value.get("template_path")
            else None,
        }
        for key, value in VIDEO_TYPES.items()
    }

    @app.after_request
    def add_no_cache_headers(response):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/")
    def index():
        active_video_type = next(
            (key for key, value in app.config["VIDEO_TYPES"].items() if value["enabled"]),
            "ai-photo",
        )
        return render_template(
            "index.html",
            video_types=public_video_types(),
            active_video_type=active_video_type,
        )

    @app.post("/generate")
    def generate():
        video_type_key = (request.form.get("video_type") or "ai-photo").lower()
        video_title = request.form.get("video_title") or ""

        if video_type_key not in app.config["VIDEO_TYPES"]:
            return jsonify({"error": "Unknown video type."}), 400

        video_type = app.config["VIDEO_TYPES"][video_type_key]
        if not video_type["enabled"]:
            return jsonify({"error": f"{video_type['label']} is not available yet."}), 400

        slot_definitions = {
            slot["field"]: slot
            for group in video_type["upload_groups"]
            for slot in group["slots"]
        }
        slot_fields = list(slot_definitions)

        uploaded_files = {}
        try:
            for field_name in slot_fields:
                file_storage = request.files.get(field_name)
                if file_storage is None:
                    raise ValueError("Upload all required files before generating the video.")
                input_kind = slot_definitions[field_name].get("input_kind", "image")
                ext = validate_upload(file_storage, input_kind)
                uploaded_files[field_name] = (file_storage, ext)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

        output_dir = video_type_output_dir(app.config["OUTPUT_ROOT"], video_type_key)
        output_name = build_output_filename(video_title)
        temp_dir = Path(tempfile.mkdtemp(prefix="lottie_uploads_"))
        saved_paths: dict[str, Path] = {}

        for field_name, (file_storage, ext) in uploaded_files.items():
            saved_path = temp_dir / f"{field_name}{ext}"
            file_storage.save(saved_path)
            if slot_definitions[field_name].get("input_kind") == "video":
                try:
                    duration = probe_video_duration(saved_path)
                except Exception:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    return jsonify({"error": "Could not read the uploaded MP4 file."}), 400
                if duration > MAX_VIDEO_DURATION_SECONDS:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    return jsonify(
                        {"error": f"Video uploads must be {MAX_VIDEO_DURATION_SECONDS} seconds or shorter."}
                    ), 400
            saved_paths[field_name] = saved_path

        job_id = uuid.uuid4().hex
        job = RenderJob(id=job_id, work_dir=temp_dir)
        app.config["JOBS"][job_id] = job

        worker = threading.Thread(
            target=run_render_job,
            kwargs={
                "app": app,
                "job": job,
                "video_type_key": video_type_key,
                "output_name": output_name,
                "saved_paths": saved_paths,
            },
            daemon=True,
        )
        worker.start()

        return jsonify({"jobId": job_id}), 202

    @app.get("/generate/<job_id>")
    def generate_status(job_id: str):
        job = app.config["JOBS"].get(job_id)
        if job is None:
            return jsonify({"error": "Unknown render job."}), 404
        return jsonify(serialize_job(job))

    @app.post("/generate/<job_id>/terminate")
    def terminate_generate(job_id: str):
        job = app.config["JOBS"].get(job_id)
        if job is None:
            return jsonify({"error": "Unknown render job."}), 404

        with job.lock:
            if job.status in {"cancelling", "cancelled"}:
                return jsonify(serialize_job(job))
            if job.status not in {"queued", "running"}:
                return jsonify({"error": "This render is no longer active."}), 409
            job.cancel_event.set()
            job.status = "cancelling"
            job.message = "Terminating"

        return jsonify(serialize_job(job))

    @app.get("/outputs/<video_type>/<path:filename>")
    def download_output(video_type: str, filename: str):
        if video_type not in app.config["VIDEO_TYPES"]:
            abort(404)

        output_dir = video_type_output_dir(app.config["OUTPUT_ROOT"], video_type)
        file_path = output_dir / filename
        if not file_path.exists():
            app.logger.warning("Missing output file for %s: %s", video_type, file_path)
            abort(404)
        app.logger.info("Serving output file for %s: %s", video_type, file_path)
        return send_from_directory(output_dir, filename)

    return app


app = create_app(DEFAULT_OUTPUT_ROOT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local web UI for the Lottie video renderer.")
    parser.add_argument("--host", default="127.0.0.1", help="Server host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="Server port. Default: 8000")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=f"Root directory for per-video-type output folders. Default: {DEFAULT_OUTPUT_ROOT}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cli_app = create_app(Path(args.output_dir))
    cli_app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
