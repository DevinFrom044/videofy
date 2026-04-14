#!/usr/bin/env python3

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


class RenderCancelled(RuntimeError):
    pass


def run(
    cmd: list[str],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_text = ""
    stderr_text = ""
    try:
        while True:
            if should_cancel and should_cancel():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RenderCancelled("Render was terminated.")

            return_code = process.poll()
            if return_code is not None:
                stdout_text, stderr_text = process.communicate()
                if return_code != 0:
                    raise RuntimeError(
                        f"Command failed: {' '.join(cmd)}\n"
                        f"stdout:\n{stdout_text}\n"
                        f"stderr:\n{stderr_text}"
                    )
                return

            try:
                stdout_text, stderr_text = process.communicate(timeout=0.1)
                return_code = process.returncode
                if return_code != 0:
                    raise RuntimeError(
                        f"Command failed: {' '.join(cmd)}\n"
                        f"stdout:\n{stdout_text}\n"
                        f"stderr:\n{stderr_text}"
                    )
                return
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def run_with_progress(
    cmd: list[str],
    *,
    progress_prefix: str,
    on_progress: Callable[[str], None],
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=False,
        bufsize=0,
    )

    output_chunks: list[str] = []
    line_buffer = ""

    assert process.stdout is not None
    os.set_blocking(process.stdout.fileno(), False)
    try:
        while True:
            if should_cancel and should_cancel():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RenderCancelled("Render was terminated.")

            try:
                chunk = process.stdout.read()
            except BlockingIOError:
                chunk = None

            if chunk:
                decoded = chunk.decode("utf-8", errors="replace")
                output_chunks.append(decoded)
                line_buffer += decoded
                while "\n" in line_buffer:
                    line, line_buffer = line_buffer.split("\n", 1)
                    stripped = line.strip()
                    if stripped.startswith(progress_prefix):
                        on_progress(stripped)
                continue

            if process.poll() is not None:
                remainder = process.stdout.read()
                if remainder:
                    decoded = remainder.decode("utf-8", errors="replace")
                    output_chunks.append(decoded)
                    line_buffer += decoded
                if line_buffer.strip().startswith(progress_prefix):
                    on_progress(line_buffer.strip())
                break
            time.sleep(0.05)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    if process.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"output:\n{''.join(output_chunks)}"
        )


def find_bitmap_assets(lottie: dict) -> list[dict]:
    return [asset for asset in lottie.get("assets", []) if isinstance(asset.get("p"), str)]


def asset_map_by_id(bitmap_assets: list[dict]) -> dict[str, dict]:
    return {asset["id"]: asset for asset in bitmap_assets}


def default_asset_ids(bitmap_assets: list[dict]) -> tuple[str, str]:
    if len(bitmap_assets) < 2:
        raise RuntimeError("The template must contain at least two image assets.")
    return bitmap_assets[1]["id"], bitmap_assets[0]["id"]


def preprocess_image(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output_path),
        ],
        should_cancel=should_cancel,
    )


def create_transparent_image(
    output_path: Path,
    width: int,
    height: int,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black@0.0:s={width}x{height}:d=1",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgba",
            str(output_path),
        ],
        should_cancel=should_cancel,
    )


def infer_asset_scale(lottie: dict, asset_id: str) -> tuple[float, float]:
    for container in lottie.get("assets", []):
        layers = container.get("layers")
        if not layers:
            continue

        image_layer = next(
            (layer for layer in layers if layer.get("ty") == 2 and layer.get("refId") == asset_id),
            None,
        )
        if image_layer is None:
            continue

        layer_by_index = {layer.get("ind"): layer for layer in layers if layer.get("ind") is not None}
        scale_x = 1.0
        scale_y = 1.0
        current = image_layer
        while current is not None:
            ks = current.get("ks", {})
            scale = ks.get("s", {}).get("k")
            if isinstance(scale, list) and len(scale) >= 2:
                scale_x *= float(scale[0]) / 100.0
                scale_y *= float(scale[1]) / 100.0
            parent_index = current.get("parent")
            current = layer_by_index.get(parent_index)
        return scale_x, scale_y

    raise RuntimeError(f"Could not infer scale for asset '{asset_id}'.")


def preprocess_vertical_image(
    input_path: Path,
    output_path: Path,
    *,
    lottie: dict,
    asset_id: str,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    bitmap_assets = asset_map_by_id(find_bitmap_assets(lottie))
    asset = bitmap_assets[asset_id]
    asset_width = int(asset["w"])
    asset_height = int(asset["h"])
    frame_width = int(lottie["w"])
    frame_height = int(lottie["h"])
    scale_x, scale_y = infer_asset_scale(lottie, asset_id)
    visible_width = max(1, round(frame_width / scale_x))
    visible_height = max(1, round(frame_height / scale_y))
    vf = (
        f"scale={visible_width}:{visible_height}:force_original_aspect_ratio=increase,"
        f"crop={visible_width}:{visible_height},"
        f"pad={asset_width}:{asset_height}:(ow-iw)/2:(oh-ih)/2"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output_path),
        ],
        should_cancel=should_cancel,
    )


def encode_data_uri(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def replace_asset_image(asset: dict, prepared_image: Path) -> None:
    asset["p"] = encode_data_uri(prepared_image)
    asset["u"] = ""
    asset["e"] = 1


def hide_layers_by_index(lottie: dict, layer_indices: list[int]) -> None:
    if not layer_indices:
        return
    hidden = set(layer_indices)
    for layer in lottie.get("layers", []):
        if layer.get("ind") in hidden:
            layer["hd"] = True


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


@dataclass
class RenderResult:
    output_video: Path
    rendered_json: Path
    replaced_asset_ids: list[str]


def compose_video_background_with_overlay(
    *,
    input_video_path: Path,
    overlay_frames_dir: Path,
    output_video_path: Path,
    width: int,
    height: int,
    framerate: float,
    duration_seconds: float,
    video_crf: int,
    video_preset: str,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    filter_complex = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={framerate},"
        f"tpad=stop_mode=clone:stop_duration={duration_seconds}[bg];"
        f"[1:v]scale={width}:{height}:flags=lanczos,"
        f"colorkey=0x00FF00:0.03:0.0,"
        f"despill=type=green:mix=0.25:expand=0.0[fg];"
        f"[bg][fg]overlay=0:0:format=auto,"
        f"pad=ceil(iw/2)*2:ceil(ih/2)*2[out]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video_path),
            "-framerate",
            str(framerate),
            "-i",
            str(overlay_frames_dir / "frame_%05d.png"),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-t",
            f"{duration_seconds:.6f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            video_preset,
            "-crf",
            str(video_crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_video_path),
        ],
        should_cancel=should_cancel,
    )


def render_video(
    *,
    template_path: Path,
    asset_image_paths: Mapping[str, Path],
    output_dir: Path,
    output_name: str = "render.mp4",
    chrome_path: str | None = None,
    scale_factor: int = 2,
    video_crf: int = 18,
    video_preset: str = "slow",
    image_modes_by_asset_id: Mapping[str, str] | None = None,
    transparent_asset_ids: list[str] | None = None,
    hidden_layer_inds: list[int] | None = None,
    overlay_video_path: Path | None = None,
    transparent_background: bool = False,
    keep_temp: bool = False,
    progress_callback: Callable[[int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RenderResult:
    transparent_asset_ids = list(transparent_asset_ids or [])
    hidden_layer_inds = list(hidden_layer_inds or [])

    if not asset_image_paths and not transparent_asset_ids:
        raise RuntimeError("At least one asset replacement must be provided.")

    project_dir = Path(__file__).resolve().parent
    template_path = template_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    lottie = json.loads(template_path.read_text(encoding="utf-8"))
    bitmap_by_id = asset_map_by_id(find_bitmap_assets(lottie))
    requested_asset_ids = list(asset_image_paths) + transparent_asset_ids
    missing_asset_ids = [asset_id for asset_id in requested_asset_ids if asset_id not in bitmap_by_id]
    if missing_asset_ids:
        joined = ", ".join(sorted(missing_asset_ids))
        raise RuntimeError(f"Template is missing bitmap asset ids: {joined}")

    image_modes_by_asset_id = dict(image_modes_by_asset_id or {})

    temp_dir_obj = tempfile.TemporaryDirectory(prefix="lottie_render_")
    temp_dir = Path(temp_dir_obj.name)
    frames_dir = temp_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        total_assets = len(asset_image_paths) + len(transparent_asset_ids)
        processed_assets = 0
        for asset_id, input_image in asset_image_paths.items():
            source_path = Path(input_image).expanduser().resolve()
            prepared_image = temp_dir / f"asset_{asset_id}.png"
            asset = bitmap_by_id[asset_id]
            image_mode = image_modes_by_asset_id.get(asset_id, "cover")

            if image_mode == "vertical_fill_height":
                preprocess_vertical_image(
                    source_path,
                    prepared_image,
                    lottie=lottie,
                    asset_id=asset_id,
                    should_cancel=should_cancel,
                )
            else:
                preprocess_image(
                    source_path,
                    prepared_image,
                    int(asset["w"]),
                    int(asset["h"]),
                    should_cancel=should_cancel,
                )

            replace_asset_image(asset, prepared_image)
            processed_assets += 1
            if progress_callback:
                progress_value = max(5, round((processed_assets / total_assets) * 20))
                progress_callback(progress_value, "Preparing images")

        for asset_id in transparent_asset_ids:
            prepared_image = temp_dir / f"asset_{asset_id}_transparent.png"
            asset = bitmap_by_id[asset_id]
            create_transparent_image(
                prepared_image,
                int(asset["w"]),
                int(asset["h"]),
                should_cancel=should_cancel,
            )
            replace_asset_image(asset, prepared_image)
            processed_assets += 1
            if progress_callback:
                progress_value = max(5, round((processed_assets / total_assets) * 20))
                progress_callback(progress_value, "Preparing images")

        hide_layers_by_index(lottie, hidden_layer_inds)

        rendered_json_path = output_dir / f"{Path(output_name).stem}.json"
        write_json(rendered_json_path, lottie)
        if progress_callback:
            progress_callback(22, "Preparing render")

        node_script = project_dir / "render_frames.js"
        node_cmd = [
            "node",
            str(node_script),
            "--lottie",
            str(rendered_json_path),
            "--output-dir",
            str(frames_dir),
            "--width",
            str(int(lottie["w"])),
            "--height",
            str(int(lottie["h"])),
            "--frames",
            str(int(lottie["op"])),
            "--scale-factor",
            str(scale_factor),
            "--transparent",
            "1" if transparent_background else "0",
            *(["--chrome-path", chrome_path] if chrome_path else []),
        ]

        if progress_callback:
            total_frames = int(lottie["op"])

            def handle_frame_progress(message: str) -> None:
                _, frame_str, total_str = message.split()
                frame_number = int(frame_str)
                frame_total = int(total_str) if total_str else total_frames
                ratio = frame_number / max(1, frame_total)
                progress_value = 22 + round(ratio * 68)
                progress_callback(progress_value, f"Rendering frames {frame_number}/{frame_total}")

            run_with_progress(
                node_cmd,
                progress_prefix="FRAME_PROGRESS",
                on_progress=handle_frame_progress,
                should_cancel=should_cancel,
            )
        else:
            run(node_cmd, should_cancel=should_cancel)

        output_video = output_dir / output_name
        if overlay_video_path:
            if progress_callback:
                progress_callback(92, "Compositing video")
            compose_video_background_with_overlay(
                input_video_path=Path(overlay_video_path).expanduser().resolve(),
                overlay_frames_dir=frames_dir,
                output_video_path=output_video,
                width=int(lottie["w"]),
                height=int(lottie["h"]),
                framerate=float(lottie["fr"]),
                duration_seconds=int(lottie["op"]) / float(lottie["fr"]),
                video_crf=video_crf,
                video_preset=video_preset,
                should_cancel=should_cancel,
            )
        else:
            if progress_callback:
                progress_callback(92, "Encoding video")
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-framerate",
                    str(lottie["fr"]),
                    "-i",
                    str(frames_dir / "frame_%05d.png"),
                    "-vf",
                    "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    video_preset,
                    "-crf",
                    str(video_crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(output_video),
                ],
                should_cancel=should_cancel,
            )

        if keep_temp:
            kept_temp_dir = output_dir / f"{Path(output_name).stem}_temp"
            if kept_temp_dir.exists():
                shutil.rmtree(kept_temp_dir)
            shutil.copytree(temp_dir, kept_temp_dir)
        if progress_callback:
            progress_callback(100, "Done")
    finally:
        temp_dir_obj.cleanup()

    return RenderResult(
        output_video=output_video,
        rendered_json=rendered_json_path,
        replaced_asset_ids=list(asset_image_paths.keys()) + transparent_asset_ids,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace image assets in a Lottie template and render MP4."
    )
    parser.add_argument("--template", required=True, help="Path to the source Lottie JSON template.")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where the rendered MP4 and generated JSON will be written.",
    )
    parser.add_argument(
        "--output-name",
        default="render.mp4",
        help="Rendered MP4 filename. Default: render.mp4",
    )
    parser.add_argument(
        "--asset-image",
        action="append",
        nargs=2,
        metavar=("ASSET_ID", "IMAGE_PATH"),
        help="Replace a bitmap asset id with the provided image. Repeat for multiple assets.",
    )
    parser.add_argument("--before", default=None, help="Legacy Before image path.")
    parser.add_argument("--after", default=None, help="Legacy After image path.")
    parser.add_argument("--before-asset-id", default=None, help="Legacy Before asset id override.")
    parser.add_argument("--after-asset-id", default=None, help="Legacy After asset id override.")
    parser.add_argument(
        "--chrome-path",
        default=None,
        help="Optional path to Chrome/Chromium executable.",
    )
    parser.add_argument(
        "--scale-factor",
        type=int,
        default=2,
        help="Frame render scale multiplier. Default: 2",
    )
    parser.add_argument(
        "--video-crf",
        type=int,
        default=18,
        help="H.264 quality factor. Lower is better. Default: 18",
    )
    parser.add_argument(
        "--video-preset",
        default="slow",
        help="FFmpeg x264 preset. Default: slow",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary frames and prepared images for debugging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    asset_image_paths: dict[str, Path] = {}
    if args.asset_image:
        asset_image_paths = {asset_id: Path(image_path) for asset_id, image_path in args.asset_image}
    elif args.before and args.after:
        lottie = json.loads(Path(args.template).expanduser().read_text(encoding="utf-8"))
        default_before, default_after = default_asset_ids(find_bitmap_assets(lottie))
        before_asset_id = args.before_asset_id or default_before
        after_asset_id = args.after_asset_id or default_after
        asset_image_paths = {
            before_asset_id: Path(args.before),
            after_asset_id: Path(args.after),
        }
    else:
        raise RuntimeError("Provide either --asset-image pairs or both --before and --after.")

    result = render_video(
        template_path=Path(args.template),
        asset_image_paths=asset_image_paths,
        output_dir=Path(args.output_dir),
        output_name=args.output_name,
        chrome_path=args.chrome_path,
        scale_factor=args.scale_factor,
        video_crf=args.video_crf,
        video_preset=args.video_preset,
        keep_temp=args.keep_temp,
    )

    print(f"Rendered video: {result.output_video}")
    print(f"Generated Lottie JSON: {result.rendered_json}")
    print(f"Replaced asset ids: {', '.join(result.replaced_asset_ids)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
