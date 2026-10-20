"""Frame downloads and additive annotation operations. No credentials stored here."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile


def serial(value):
    from cvat_sdk.api_client import ApiClient

    return ApiClient.sanitize_for_serialization(value)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_new(path, value):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    return path


def safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("Invalid frame filename")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or ":" in name
        or path.as_posix() == "."
    ):
        raise ValueError("Unsafe frame filename")
    return path


def frame_catalog(task):
    meta = task.get_meta()
    frames = list(meta.frames)
    size = task.size
    if type(size) is not int or size < 1:
        raise ValueError("Task has no frames")
    # Video metadata can describe only one common image size, not every frame.
    if len(frames) == size:
        names = [f.name for f in frames]
    elif len(frames) == 1 and size > 1:
        names = [f"frame_{i:06d}.jpg" for i in range(size)]
    else:
        raise ValueError("Frame metadata does not match task size")
    normalized = [safe_name(n).as_posix() for n in names]
    if len({n.casefold() for n in normalized}) != len(names):
        raise ValueError("Frame names collide; refusing to overwrite files")
    return meta, normalized


def parse_frame_ids(value, size):
    if value is None:
        return list(range(size))
    selected = set()
    for part in value.split(","):
        bounds = part.split(":")
        if len(bounds) == 1:
            selected.add(int(bounds[0]))
        elif len(bounds) == 2:
            start, stop = map(int, bounds)
            if start > stop:
                raise ValueError("Invalid frame range")
            if start < 0 or stop >= size:
                raise ValueError("Frame outside task")
            selected.update(range(start, stop + 1))
        else:
            raise ValueError("Use frame IDs like 0,2,5:9 (inclusive)")
    if not selected or min(selected) < 0 or max(selected) >= size:
        raise ValueError("Frame outside task")
    return sorted(selected)


def download_frames(task, out, selection=None, quality="original"):
    output = Path(out).expanduser().resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    meta, names = frame_catalog(task)
    ids = parse_frame_ids(selection, task.size)
    deleted = set(meta.deleted_frames or [])
    if deleted.intersection(ids):
        raise ValueError("Selection includes deleted frames; select active frame IDs")
    stage = Path(tempfile.mkdtemp(prefix=".cvat-frames-", dir=output.parent))
    rows = []
    try:
        for i in ids:
            data = task.get_frame(i, quality=quality).read()
            if not data:
                raise ValueError("Server returned an empty frame")
            source_name = names[i]
            name = source_name
            if data.startswith(b"\xff\xd8") and Path(name).suffix.lower() not in (
                ".jpg",
                ".jpeg",
                ".jpe",
            ):
                name = str(PurePosixPath(name).with_suffix(".jpg"))
            elif (
                data.startswith(b"\x89PNG\r\n\x1a\n")
                and Path(name).suffix.lower() != ".png"
            ):
                name = str(PurePosixPath(name).with_suffix(".png"))
            target = stage.joinpath(*safe_name(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("Frame names collide after encoding detection")
            target.write_bytes(data)
            rows.append(
                {
                    "frame": i,
                    "name": name,
                    "source_name": source_name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        write_new(
            stage / "frames_manifest.json",
            {"task_id": task.id, "quality": quality, "frames": rows},
        )
        if output.exists():
            raise ValueError("Output appeared during download; refusing to replace it")
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"task_id": task.id, "directory": str(output), "frames": len(rows)}


def attributes(values, label):
    lookup = {a.name: a for a in label.attributes}
    if not isinstance(values, dict):
        raise ValueError("Attributes must map attribute names to values")
    resolved = []
    for name, value in values.items():
        if name not in lookup:
            raise ValueError(f"Unknown attribute: {name}")
        attribute = lookup[name]
        value = str(value).lower() if type(value) is bool else str(value)
        if (
            attribute.input_type in ("select", "radio")
            and value not in attribute.values
        ):
            raise ValueError(f"Invalid value for {label.name}.{name}")
        if attribute.input_type == "checkbox" and value not in ("true", "false"):
            raise ValueError("Checkbox attribute must be true or false")
        if attribute.input_type == "number":
            try:
                number = float(value)
                minimum, maximum, step = map(float, attribute.values)
            except (ValueError, TypeError):
                raise ValueError("Invalid numeric attribute") from None
            if (
                not math.isfinite(number)
                or not minimum <= number <= maximum
                or step <= 0
            ):
                raise ValueError("Numeric attribute outside its domain")
            if not math.isclose(
                (number - minimum) / step,
                round((number - minimum) / step),
                abs_tol=1e-7,
            ):
                raise ValueError("Numeric attribute violates its step")
        resolved.append({"spec_id": attribute.id, "value": value})
    return resolved


def resolve(payload, labels, size):
    if not isinstance(payload, dict) or set(payload) - {
        "shapes",
        "tags",
        "tracks",
        "version",
    }:
        raise ValueError("Expected an annotation object with shapes/tags")
    if payload.get("tracks"):
        raise ValueError(
            "Track JSON is not supported; use CVAT import in an empty task"
        )
    lookup = {label.name: label for label in labels}
    result = {"version": 0, "shapes": [], "tags": [], "tracks": []}
    for kind in ("shapes", "tags"):
        if not isinstance(payload.get(kind, []), list):
            raise ValueError(f"{kind} must be a list")
        for item in payload.get(kind, []):
            item = deepcopy(item)
            allowed = {"label", "frame", "attributes", "group", "source"}
            if kind == "shapes":
                allowed |= {
                    "type",
                    "points",
                    "occluded",
                    "outside",
                    "z_order",
                    "rotation",
                }
            if set(item) - allowed:
                raise ValueError(
                    "Unknown annotation fields; use labels and attribute names, not server IDs"
                )
            label_name = item.pop("label", None)
            if label_name not in lookup:
                raise ValueError(f"Unknown label: {label_name}")
            label = lookup[label_name]
            frame = item.get("frame")
            if type(frame) is not int or not 0 <= frame < size:
                raise ValueError("Annotation frame outside task")
            item["attributes"] = attributes(item.get("attributes", {}), label)
            item["label_id"] = label.id
            item["source"] = "auto"
            if kind == "shapes":
                shape_type, points = item.get("type"), item.get("points")
                if (
                    not isinstance(points, list)
                    or len(points) % 2
                    or not all(
                        type(v) in (int, float) and math.isfinite(v) for v in points
                    )
                ):
                    raise ValueError("Expected finite coordinate pairs")
                valid = {
                    "rectangle": len(points) == 4,
                    "polygon": len(points) >= 6,
                    "polyline": len(points) >= 4,
                    "points": len(points) >= 2,
                    "ellipse": len(points) == 4,
                    "cuboid": len(points) == 16,
                }
                if not valid.get(shape_type, False):
                    raise ValueError("Invalid shape type or coordinate count")
                if shape_type == "rectangle" and not (
                    points[0] < points[2] and points[1] < points[3]
                ):
                    raise ValueError("Expected positive rectangle coordinates")
                if shape_type == "polygon":
                    if len(set(zip(points[::2], points[1::2]))) < 3:
                        raise ValueError("Polygon needs three distinct vertices")
                    area = sum(
                        points[2 * i] * points[2 * ((i + 1) % (len(points) // 2)) + 1]
                        - points[2 * ((i + 1) % (len(points) // 2))] * points[2 * i + 1]
                        for i in range(len(points) // 2)
                    )
                    if abs(area) < 1e-9:
                        raise ValueError("Polygon has zero area")
                if shape_type == "ellipse" and not (
                    points[2] > points[0] and points[3] < points[1]
                ):
                    raise ValueError("Expected positive ellipse radii")
                for field in ("group", "z_order"):
                    if field in item and type(item[field]) is not int:
                        raise ValueError(f"{field} must be an integer")
                if item.get("group", 0) < 0:
                    raise ValueError("Group must be nonnegative")
                for field in ("occluded", "outside"):
                    if field in item and type(item[field]) is not bool:
                        raise ValueError(f"{field} must be boolean")
                if "rotation" in item and (
                    type(item["rotation"]) not in (int, float)
                    or not math.isfinite(item["rotation"])
                ):
                    raise ValueError("Rotation must be finite")
            result[kind].append(item)
    return result


def read_cvat_images(path, names):
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            candidates = [
                n
                for n in archive.namelist()
                if PurePosixPath(n).name == "annotations.xml"
            ]
            if len(candidates) != 1:
                raise ValueError("Expected exactly one annotations.xml in ZIP")
            info = archive.getinfo(candidates[0])
            if info.file_size > 100_000_000:
                raise ValueError("Annotation XML too large")
            data = archive.read(info)
    else:
        if path.stat().st_size > 100_000_000:
            raise ValueError("Annotation XML too large")
        data = path.read_bytes()
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML entities and DTDs are not accepted")
    root = ET.fromstring(data)
    if root.tag != "annotations" or root.findall("track"):
        raise ValueError("Only CVAT for images XML is supported by import-missing")
    lookup = {name: i for i, name in enumerate(names)}
    if len(lookup) != len(names):
        raise ValueError("Duplicate destination image names")
    shapes, tags, seen = [], [], set()
    for image in root.findall("image"):
        name = image.attrib["name"]
        if name not in lookup or name in seen:
            raise ValueError("XML image name missing or duplicated in destination task")
        seen.add(name)
        for child in image:
            values = {
                a.attrib["name"]: a.text or "" for a in child.findall("attribute")
            }
            common = {
                "label": child.attrib["label"],
                "frame": lookup[name],
                "attributes": values,
            }
            if child.tag == "tag":
                tags.append(common)
                continue
            if child.tag == "box":
                points = [float(child.attrib[k]) for k in ("xtl", "ytl", "xbr", "ybr")]
                kind = "rectangle"
            elif child.tag in ("polygon", "polyline", "points"):
                points = [
                    float(v)
                    for pair in child.attrib["points"].split(";")
                    for v in pair.split(",")
                ]
                kind = child.tag
            elif child.tag == "ellipse":
                cx, cy, rx, ry = (
                    float(child.attrib[k]) for k in ("cx", "cy", "rx", "ry")
                )
                points = [cx, cy, cx + rx, cy - ry]
                kind = "ellipse"
            else:
                raise ValueError(f"Unsupported XML shape: {child.tag}")
            common.update(
                type=kind,
                points=points,
                occluded=child.attrib.get("occluded", "0") == "1",
                z_order=int(child.attrib.get("z_order", "0")),
                rotation=float(child.attrib.get("rotation", "0")),
                group=int(child.attrib.get("group_id", "0")),
            )
            shapes.append(common)
    return {"shapes": shapes, "tags": tags}


def occupied_frames(annotations, size):
    frames = {
        item["frame"]
        for kind in ("shapes", "tags")
        for item in annotations.get(kind, [])
    }

    # Conservatively reserve from the first track keyframe through the task end.
    def track_frames(track):
        keys = [s["frame"] for s in track.get("shapes", [])]
        if keys:
            frames.update(range(min(keys), size))
        for element in track.get("elements", []):
            track_frames(element)

    for track in annotations.get("tracks", []):
        track_frames(track)
    return frames


def filter_missing(payload, current, size):
    occupied = occupied_frames(current, size)
    result = deepcopy(payload)
    for kind in ("shapes", "tags"):
        result[kind] = [s for s in result[kind] if s["frame"] not in occupied]
    return result, sorted(occupied)


def shape_key(item):
    keys = (
        "frame",
        "label_id",
        "source",
        "group",
        "occluded",
        "outside",
        "z_order",
        "rotation",
    )
    defaults = {
        "source": "auto",
        "group": 0,
        "occluded": False,
        "outside": False,
        "z_order": 0,
        "rotation": 0.0,
    }
    core = {k: item.get(k, defaults.get(k)) for k in keys}
    core["type"] = item.get("type")
    core["points"] = [round(float(p), 3) for p in item.get("points", [])]
    core["attributes"] = sorted(item.get("attributes", []), key=lambda a: a["spec_id"])
    return canonical(core)


def verify_created(before, after, payload):
    for kind in ("shapes", "tags", "tracks"):
        previous = {a["id"]: a for a in before.get(kind, [])}
        saved = {a["id"]: a for a in after.get(kind, [])}
        if not all(saved.get(k) == v for k, v in previous.items()):
            raise ValueError(
                "An existing annotation changed; inspect backup and saved state"
            )
        added = [a for a in after.get(kind, []) if a["id"] not in previous]
        expected = payload.get(kind, [])
        if Counter(map(shape_key, added)) != Counter(map(shape_key, expected)):
            raise ValueError(
                "Saved additions differ from requested payload; inspect receipt before retrying"
            )


def append_annotations(
    client, task, payload, *, apply=False, backup=None, receipt=None
):
    current = serial(task.get_annotations())
    resolved = resolve(payload, task.get_labels(), task.size)
    filtered, occupied = filter_missing(resolved, current, task.size)
    filtered["version"] = current["version"]
    summary = {
        "task_id": task.id,
        "operation": "create",
        "apply": apply,
        "shapes": len(filtered["shapes"]),
        "tags": len(filtered["tags"]),
        "frames": sorted({s["frame"] for k in ("shapes", "tags") for s in filtered[k]}),
        "preserved_frames": occupied,
        "snapshot_sha256": digest(current),
    }
    if not apply or not (filtered["shapes"] or filtered["tags"]):
        return summary
    if backup is None or receipt is None:
        raise ValueError("--apply requires --backup and --receipt")
    backup, receipt = (
        Path(backup).expanduser().resolve(),
        Path(receipt).expanduser().resolve(),
    )
    saved_path = receipt.with_name(receipt.name + ".saved.json")
    if backup == receipt or backup.exists() or receipt.exists() or saved_path.exists():
        raise ValueError("Backup and receipt must be distinct, new files")
    write_new(
        backup,
        {
            "task_id": task.id,
            "annotations": current,
            "labels": {str(label.id): label.name for label in task.get_labels()},
        },
    )
    if serial(task.get_annotations()) != current:
        raise ValueError(
            "Annotations changed during preparation; reconcile before sending"
        )
    from cvat_sdk.api_client import models

    request = models.PatchedLabeledDataRequest(
        version=filtered["version"],
        tracks=[],
        shapes=[models.LabeledShapeRequest(**s) for s in filtered["shapes"]],
        tags=[models.LabeledImageRequest(**s) for s in filtered["tags"]],
    )
    write_new(
        receipt,
        {**summary, "state": "request_started", "automatic_retry_forbidden": True},
    )
    created, response = client.api_client.tasks_api.partial_update_annotations(
        action="create", id=task.id, patched_labeled_data_request=request
    )
    receipt.write_text(
        json.dumps(
            {
                **summary,
                "state": "created_pending_verification",
                "http_status": response.status,
                "response": serial(created),
            },
            indent=2,
        )
    )
    saved = serial(task.get_annotations())
    saved_path = receipt.with_name(receipt.name + ".saved.json")
    write_new(saved_path, saved)
    verify_created(current, saved, filtered)
    result = {
        **summary,
        "state": "verified",
        "http_status": response.status,
        "backup": str(backup),
        "saved": str(saved_path),
    }
    receipt.write_text(json.dumps(result, indent=2))
    return result
