"""The public Getter CVAT command-line interface.

Secrets are read only from env/profile and never logged.
"""

import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys

from sampling import uniform_indices

DEFAULT_ENV = Path.home() / ".config/getter-cvat/.env"


def read_env(path):
    values = {}
    if path.exists():
        if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError(f"Restrict permissions first: chmod 600 {path}")
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError("Invalid env line (expected KEY=value)")
            values[key.strip()] = value.strip().strip("\"'")
    return values


def resources(paths):
    result = []
    extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".webp",
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".zip",
    }
    for value in paths:
        p = Path(value).expanduser().resolve()
        if p.is_dir():
            result.extend(
                str(f)
                for f in sorted(p.iterdir())
                if f.suffix.lower() in extensions and f.is_file()
            )
        elif p.is_file():
            result.append(str(p))
        else:
            raise ValueError(f"File not found: {p}")
    if not result or len(result) != len(set(result)):
        raise ValueError("Empty or duplicate resources")
    if len({Path(p).name for p in result}) != len(result):
        raise ValueError("Resource basenames must be unique")
    return result


def json_file(path):
    return json.loads(Path(path).read_text())


def ensure_empty(task):
    current = task.get_annotations()
    if current.shapes or current.tracks or current.tags:
        raise ValueError(
            "Task already has annotations. Refusing to overwrite; export/review first."
        )


def resolve_annotations(payload, labels, size):
    from cvat_core import resolve

    return resolve(payload, labels, size)


def sample(args):
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames,r_frame_rate",
                "-of",
                "json",
                args.video,
            ]
        )
    )["streams"][0]
    total = int(probe["nb_read_frames"])
    indices = uniform_indices(total, args.count)
    numerator, denominator = map(int, probe["r_frame_rate"].split("/"))
    fps = numerator / denominator
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    expression = "+".join(f"eq(n,{i})" for i in indices)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-n",
            "-i",
            args.video,
            "-vf",
            f"select='{expression}'",
            "-fps_mode",
            "vfr",
            "-q:v",
            "2",
            "-start_number",
            "0",
            str(out / "frame_%06d.jpg"),
        ],
        check=True,
    )
    manifest = [
        {
            "frame": n,
            "file": f"frame_{n:06d}.jpg",
            "source_frame": i,
            "source_seconds": i / fps,
        }
        for n, i in enumerate(indices)
    ]
    if len(list(out.glob("frame_*.jpg"))) != args.count:
        raise ValueError("Extracted frame count mismatch")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"directory": str(out), "count": args.count, "fps": fps}))


def parser():
    p = argparse.ArgumentParser(description="Getter command-line interface for CVAT")
    p.add_argument("--version", action="version", version="getter-cvat 0.3.0")
    p.add_argument("--env", type=Path, default=DEFAULT_ENV)
    p.add_argument("--host")
    p.add_argument("--org", help="Organization slug; omitted = personal workspace")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("ping")
    commands.add_parser("formats")
    j = commands.add_parser("jobs")
    j.add_argument("--task", type=int, required=True)
    j = commands.add_parser("job-info")
    j.add_argument("id", type=int)
    f = commands.add_parser(
        "frames", help="Download frame bytes without dataset export"
    )
    f.add_argument("id", type=int)
    f.add_argument("--out", required=True)
    f.add_argument("--frames", help="Zero-based IDs: 0,2,5:9 (inclusive)")
    f.add_argument("--quality", choices=["original", "compressed"], default="original")
    for name in ("append", "import-missing"):
        a = commands.add_parser(
            name, help="Add annotations only to unannotated frames; preview by default"
        )
        a.add_argument("id", type=int)
        if name == "append":
            a.add_argument("--json", required=True)
        else:
            a.add_argument("file", help="CVAT for images 1.1 XML or ZIP")
        mode = a.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument(
            "--dry-run", action="store_true", help="Preview (also the default)"
        )
        a.add_argument("--backup")
        a.add_argument("--receipt")
    for name in ["projects", "tasks"]:
        s = commands.add_parser(name)
        s.add_argument("--project", type=int)
    s = commands.add_parser("project-create")
    s.add_argument("name")
    s.add_argument("--labels", required=True)
    s = commands.add_parser("project-rename")
    s.add_argument("id", type=int)
    s.add_argument("name")
    s = commands.add_parser("task-create")
    s.add_argument("name")
    s.add_argument("--project", type=int, required=True)
    s.add_argument("--files", nargs="+", required=True)
    s.add_argument("--step", type=int, default=1)
    s.add_argument("--quality", type=int, default=100)
    s = commands.add_parser("task-upload")
    s.add_argument("id", type=int)
    s.add_argument("--files", nargs="+", required=True)
    for name in ["task-info", "labels"]:
        s = commands.add_parser(name)
        s.add_argument("id", type=int)
    s = commands.add_parser("annotate")
    s.add_argument("id", type=int)
    s.add_argument("--json", required=True)
    s = commands.add_parser("import")
    s.add_argument("id", type=int)
    s.add_argument("file")
    s.add_argument("--format", default="CVAT 1.1")
    s = commands.add_parser("export")
    s.add_argument("id", type=int)
    s.add_argument("out")
    s.add_argument("--format", default="CVAT for images 1.1")
    s.add_argument("--images", action="store_true")
    s = commands.add_parser("raw-export")
    s.add_argument("id", type=int)
    s.add_argument("out")
    s = commands.add_parser("sample")
    s.add_argument("video")
    s.add_argument("--count", type=int, default=200)
    s.add_argument("--out", required=True)
    return p


def job_summary(job):
    return {
        "id": job.id,
        "task_id": job.task_id,
        "start": job.start_frame,
        "stop": job.stop_frame,
        "state": job.state,
        "stage": job.stage,
        "type": job.type,
    }


def main():
    args = parser().parse_args()
    if args.command == "sample":
        sample(args)
        return
    from cvat_sdk import make_client
    from cvat_sdk.core.helpers import get_paginated_collection
    from cvat_sdk.core.proxies.tasks import ResourceType

    settings = read_env(args.env)
    settings.update({k: v for k, v in os.environ.items() if k.startswith("CVAT_")})
    host = (args.host or settings.get("CVAT_HOST", "")).rstrip("/")
    if not host.startswith("https://"):
        raise ValueError("HTTPS CVAT_HOST required")
    username = settings.get("CVAT_USERNAME")
    if not username:
        raise ValueError("CVAT_USERNAME required")
    password = settings.get("CVAT_PASSWORD") or getpass.getpass("CVAT password: ")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with make_client(host, credentials=(username, password)) as client:
        password = None
        client.organization_slug = args.org or ""
        cmd = args.command
        if cmd == "formats":
            from cvat_core import serial

            data, _ = client.api_client.server_api.retrieve_annotation_formats()
            print(json.dumps(serial(data), ensure_ascii=False))
        elif cmd == "jobs":
            task = client.tasks.retrieve(args.task)
            print(
                json.dumps(
                    [job_summary(j) for j in task.get_jobs()], ensure_ascii=False
                )
            )
        elif cmd == "job-info":
            job = client.jobs.retrieve(args.id)
            print(json.dumps(job_summary(job), ensure_ascii=False))
        elif cmd == "ping":
            print(json.dumps({"host": host, "username": username, "connected": True}))
        elif cmd == "projects":
            print(
                json.dumps(
                    [{"id": p.id, "name": p.name} for p in client.projects.list()],
                    ensure_ascii=False,
                )
            )
        elif cmd == "tasks":
            print(
                json.dumps(
                    [
                        {
                            "id": t.id,
                            "name": t.name,
                            "project_id": t.project_id,
                            "size": t.size,
                        }
                        for t in get_paginated_collection(
                            client.api_client.tasks_api.list_endpoint,
                            **(
                                {"project_id": args.project}
                                if args.project is not None
                                else {}
                            ),
                        )
                    ],
                    ensure_ascii=False,
                )
            )
        elif cmd == "project-create":
            if any(
                p.name.casefold() == args.name.casefold()
                for p in client.projects.list()
            ):
                raise ValueError("Project name already exists; reuse its ID")
            project = client.projects.create(
                {"name": args.name, "labels": json_file(args.labels)}
            )
            print(
                json.dumps({"id": project.id, "url": f"{host}/projects/{project.id}"})
            )
        elif cmd == "project-rename":
            project = client.projects.retrieve(args.id)
            project.update({"name": args.name})
            print(project.name)
        elif cmd == "task-create":
            files = resources(args.files)
            if args.step < 1 or not 1 <= args.quality <= 100:
                raise ValueError("Invalid frame step or image quality")
            client.projects.retrieve(args.project)
            if any(
                t.name == args.name and t.project_id == args.project
                for t in get_paginated_collection(
                    client.api_client.tasks_api.list_endpoint,
                    project_id=args.project,
                    name=args.name,
                )
            ):
                raise ValueError(
                    "Task already exists; inspect/resume rather than duplicate"
                )
            task = client.tasks.create({"name": args.name, "project_id": args.project})
            print(
                json.dumps(
                    {"created_task_id": task.id, "url": f"{host}/tasks/{task.id}"}
                ),
                flush=True,
            )
            task.upload_data(
                files,
                resource_type=ResourceType.LOCAL,
                params={
                    "image_quality": args.quality,
                    "sorting_method": "lexicographical",
                    "frame_step": args.step,
                },
                wait_for_completion=True,
            )
            task.fetch()
            print(json.dumps({"id": task.id, "size": task.size}))
        else:
            task = client.tasks.retrieve(args.id)
            if cmd == "frames":
                from cvat_core import download_frames

                print(
                    json.dumps(
                        download_frames(task, args.out, args.frames, args.quality),
                        ensure_ascii=False,
                    )
                )
            elif cmd in ("append", "import-missing"):
                from cvat_core import (
                    append_annotations,
                    frame_catalog,
                    read_cvat_images,
                )

                if cmd == "append":
                    payload = json_file(args.json)
                else:
                    _, names = frame_catalog(task)
                    payload = read_cvat_images(args.file, names)
                result = append_annotations(
                    client,
                    task,
                    payload,
                    apply=args.apply,
                    backup=args.backup,
                    receipt=args.receipt,
                )
                print(json.dumps(result, ensure_ascii=False))
            elif cmd == "task-upload":
                if task.size:
                    raise ValueError("Task already has data")
                task.upload_data(
                    resources(args.files),
                    resource_type=ResourceType.LOCAL,
                    params={"image_quality": 100, "sorting_method": "lexicographical"},
                    wait_for_completion=True,
                )
                task.fetch()
                print(json.dumps({"id": task.id, "size": task.size}))
            elif cmd == "task-info":
                frames = task.get_frames_info()
                ann = task.get_annotations()
                print(
                    json.dumps(
                        {
                            "id": task.id,
                            "name": task.name,
                            "size": task.size,
                            "jobs": [
                                {
                                    "id": j.id,
                                    "start": j.start_frame,
                                    "stop": j.stop_frame,
                                }
                                for j in task.get_jobs()
                            ],
                            "shapes": len(ann.shapes),
                            "tracks": len(ann.tracks),
                            "frames": [f.name for f in frames],
                        },
                        ensure_ascii=False,
                    )
                )
            elif cmd == "labels":
                print(
                    json.dumps(
                        [l.to_dict() for l in task.get_labels()], ensure_ascii=False
                    )
                )
            elif cmd == "raw-export":
                from cvat_sdk.api_client import ApiClient

                output = Path(args.out).expanduser().resolve()
                if output.exists():
                    raise ValueError("Output already exists")
                annotations = ApiClient.sanitize_for_serialization(
                    task.get_annotations()
                )
                labels = {str(label.id): label.name for label in task.get_labels()}
                output.write_text(
                    json.dumps(
                        {
                            "task_id": task.id,
                            "labels": labels,
                            "annotations": annotations,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                print(json.dumps({"id": task.id, "output": str(output)}))
            elif cmd == "annotate":
                ensure_empty(task)
                payload = resolve_annotations(
                    json_file(args.json), task.get_labels(), task.size
                )
                task.set_annotations(payload)
                saved = task.get_annotations()
                if len(saved.shapes) != len(payload["shapes"]) or len(
                    saved.tags
                ) != len(payload["tags"]):
                    raise ValueError("Annotation count verification failed")
                print(json.dumps({"id": task.id, "verified_shapes": len(saved.shapes)}))
            elif cmd == "import":
                ensure_empty(task)
                task.import_annotations(args.format, args.file)
            elif cmd == "export":
                if Path(args.out).exists():
                    raise ValueError("Output already exists")
                task.export_dataset(args.format, args.out, include_images=args.images)


def entrypoint():
    try:
        main()
    except Exception as error:
        # Do not dump HTTP request objects, authentication headers or credentials.
        if isinstance(error, (ValueError, KeyError, FileNotFoundError)):
            print(f"Error: {error}", file=sys.stderr)
        else:
            print(
                f"Error: {type(error).__name__}; inspect task state before retrying.",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
