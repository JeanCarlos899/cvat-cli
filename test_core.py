from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock
from zipfile import ZipFile

from cvat_cli import parser
from cvat_core import (
    append_annotations,
    download_frames,
    filter_missing,
    parse_frame_ids,
    read_cvat_images,
    resolve,
    safe_name,
    verify_created,
)


def labels():
    return [N(name="smoke", id=7, attributes=[])]


def polygon(frame):
    return {
        "type": "polygon",
        "label": "smoke",
        "frame": frame,
        "points": [1.1, 1, 10, 2, 9, 9, 2, 8],
        "attributes": {},
    }


class CoreTests(unittest.TestCase):
    def test_shapes_and_tags_resolved_without_changing_input(self):
        payload = {"shapes": [polygon(2)], "tags": [{"label": "smoke", "frame": 1}]}
        result = resolve(payload, labels(), 3)
        self.assertEqual(result["shapes"][0]["label_id"], 7)
        self.assertEqual(result["tags"][0]["source"], "auto")
        self.assertIn("label", payload["shapes"][0])

    def test_invalid_geometry_and_tracks(self):
        for points in (
            [1, 2],
            [1, 1, 2, 2, 3, 3],
            [True, 1, 2, 3, 3, 4],
            [1, 2, float("nan"), 3, 4, 5],
        ):
            item = {**polygon(1), "points": points}
            with self.subTest(points=points), self.assertRaises(ValueError):
                resolve({"shapes": [item]}, labels(), 3)
        with self.assertRaises(ValueError):
            resolve({"tracks": [{"shapes": []}]}, labels(), 3)
        with self.assertRaises(ValueError):
            resolve({"shapes": [{**polygon(0), "id": 99}]}, labels(), 3)

    def test_frame_ranges_and_names(self):
        self.assertEqual(parse_frame_ids("0,2,4:6,2", 8), [0, 2, 4, 5, 6])
        for value in ("-1", "8", "5:2", "0:999999999", "0:1:2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_frame_ids(value, 8)
        for value in ("../secret.png", "/tmp/image.png", "a\\b.png", "C:/a.png"):
            with self.assertRaises(ValueError):
                safe_name(value)
        self.assertEqual(str(safe_name("camera/frame.png")), "camera/frame.png")

    def test_download_atomic_preserves_bytes_and_mapping(self):
        task = N(
            id=8,
            size=2,
            get_meta=lambda: N(
                frames=[N(name="a.png"), N(name="b.png")], deleted_frames=[]
            ),
            get_frame=lambda i, quality: BytesIO(b"original-" + str(i).encode()),
        )
        with TemporaryDirectory() as d:
            out = Path(d) / "frames"
            self.assertEqual(download_frames(task, out, "1")["frames"], 1)
            self.assertEqual((out / "b.png").read_bytes(), b"original-1")
            self.assertEqual(
                json.loads((out / "frames_manifest.json").read_text())["frames"][0][
                    "frame"
                ],
                1,
            )
            with self.assertRaises(ValueError):
                download_frames(task, out)
            task.get_frame = Mock(
                side_effect=[BytesIO(b"first"), RuntimeError("network")]
            )
            with self.assertRaises(RuntimeError):
                download_frames(task, Path(d) / "failed")
            self.assertFalse((Path(d) / "failed").exists())
            self.assertFalse(list(Path(d).glob(".cvat-frames-*")))

    def test_compressed_download_uses_actual_image_extension(self):
        task = N(
            id=8,
            size=1,
            get_meta=lambda: N(frames=[N(name="a.png")], deleted_frames=[]),
            get_frame=lambda i, quality: BytesIO(b"\xff\xd8mock-jpeg"),
        )
        with TemporaryDirectory() as d:
            out = Path(d) / "frames"
            download_frames(task, out, quality="compressed")
            self.assertTrue((out / "a.jpg").exists())
            manifest = json.loads((out / "frames_manifest.json").read_text())
            self.assertEqual(manifest["frames"][0]["source_name"], "a.png")

    def test_video_metadata_and_name_collisions(self):
        task = N(
            id=8,
            size=3,
            get_meta=lambda: N(frames=[N(name="source.mp4")], deleted_frames=[]),
            get_frame=lambda i, quality: BytesIO(b"image"),
        )
        with TemporaryDirectory() as d:
            out = Path(d) / "frames"
            download_frames(task, out, "2")
            self.assertTrue((out / "frame_000002.jpg").exists())
            task.size = 2
            task.get_meta = lambda: N(
                frames=[N(name="A.png"), N(name="a.png")], deleted_frames=[]
            )
            with self.assertRaises(ValueError):
                download_frames(task, Path(d) / "collision")

    def test_tags_and_interpolated_tracks_protect_frames(self):
        current = {
            "shapes": [{"frame": 1}],
            "tags": [{"frame": 0}],
            "tracks": [{"shapes": [{"frame": 3}], "elements": []}],
        }
        payload = resolve({"shapes": [polygon(i) for i in range(6)]}, labels(), 6)
        result, protected = filter_missing(payload, current, 6)
        self.assertEqual([s["frame"] for s in result["shapes"]], [2])
        self.assertEqual(protected, [0, 1, 3, 4, 5])

    def test_xml_matches_filename_not_source_id_and_blocks_entities(self):
        xml = '<annotations><version>1.1</version><image id="999" name="second.png"><polygon label="smoke" points="1,1;9,1;9,9"/><tag label="smoke"/></image></annotations>'
        with TemporaryDirectory() as d:
            path = Path(d) / "input.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("annotations.xml", xml)
            data = read_cvat_images(path, ["first.png", "second.png"])
            self.assertEqual(data["shapes"][0]["frame"], 1)
            self.assertEqual(data["tags"][0]["frame"], 1)
            with self.assertRaises(ValueError):
                read_cvat_images(path, ["different.png"])
            entity = Path(d) / "entity.xml"
            entity.write_text('<!DOCTYPE annotations [<!ENTITY x "x">]><annotations/>')
            with self.assertRaises(ValueError):
                read_cvat_images(entity, [])

    def fake_server(self):
        current = {
            "version": 0,
            "shapes": [
                {
                    "id": 100,
                    "type": "polygon",
                    "frame": 0,
                    "label_id": 7,
                    "source": "manual",
                    "points": [0, 0, 10, 0, 10, 10],
                }
            ],
            "tags": [],
            "tracks": [],
        }
        task = N(
            id=8, size=4, get_labels=labels, get_annotations=lambda: deepcopy(current)
        )

        def create(*, action, id, patched_labeled_data_request):
            self.assertEqual(action, "create")
            self.assertEqual(id, 8)
            from cvat_core import serial

            data = serial(patched_labeled_data_request)
            for kind in ("shapes", "tags"):
                for item in data[kind]:
                    current[kind].append({**item, "id": 101 + len(current[kind])})
            return deepcopy(current), N(status=200)

        api = N(partial_update_annotations=Mock(side_effect=create))
        return N(api_client=N(tasks_api=api)), task, current, api

    def test_preview_and_verified_create_preserve_manual(self):
        client, task, current, api = self.fake_server()
        manual = deepcopy(current["shapes"][0])
        payload = {
            "shapes": [polygon(0), polygon(1)],
            "tags": [{"label": "smoke", "frame": 2}],
        }
        self.assertEqual(append_annotations(client, task, payload)["shapes"], 1)
        api.partial_update_annotations.assert_not_called()
        with TemporaryDirectory() as d:
            result = append_annotations(
                client,
                task,
                payload,
                apply=True,
                backup=Path(d) / "backup.json",
                receipt=Path(d) / "receipt.json",
            )
            self.assertEqual(result["state"], "verified")
            self.assertEqual(current["shapes"][0], manual)
            self.assertTrue((Path(d) / "receipt.json.saved.json").exists())
            append_annotations(client, task, payload)
            self.assertEqual(api.partial_update_annotations.call_count, 1)

    def test_failure_keeps_receipt_and_blocks_retry(self):
        client, task, _, api = self.fake_server()
        api.partial_update_annotations.side_effect = TimeoutError()
        with TemporaryDirectory() as d:
            backup, receipt = Path(d) / "before.json", Path(d) / "receipt.json"
            with self.assertRaises(TimeoutError):
                append_annotations(
                    client,
                    task,
                    {"shapes": [polygon(1)]},
                    apply=True,
                    backup=backup,
                    receipt=receipt,
                )
            self.assertEqual(
                json.loads(receipt.read_text())["state"], "request_started"
            )
            with self.assertRaises(ValueError):
                append_annotations(
                    client,
                    task,
                    {"shapes": [polygon(1)]},
                    apply=True,
                    backup=backup,
                    receipt=receipt,
                )
            self.assertEqual(api.partial_update_annotations.call_count, 1)

    def test_concurrent_edit_prevents_send(self):
        client, task, current, api = self.fake_server()
        changed = deepcopy(current)
        changed["tags"] = [{"id": 5, "frame": 3}]
        task.get_annotations = Mock(side_effect=[current, changed])
        with TemporaryDirectory() as d, self.assertRaises(ValueError):
            append_annotations(
                client,
                task,
                {"shapes": [polygon(1)]},
                apply=True,
                backup=Path(d) / "before.json",
                receipt=Path(d) / "receipt.json",
            )
        api.partial_update_annotations.assert_not_called()

    def test_verification_rejects_unexpected_geometry(self):
        before = {"shapes": [], "tags": [], "tracks": []}
        payload = resolve({"shapes": [polygon(1)]}, labels(), 4)
        after = deepcopy(payload)
        after["shapes"][0].update(id=8, points=[1, 1, 10, 1, 2, 2])
        with self.assertRaises(ValueError):
            verify_created(before, after, payload)

    def test_parser_defaults_to_preview(self):
        self.assertFalse(
            parser().parse_args(["append", "123", "--json", "input.json"]).apply
        )
        self.assertTrue(
            parser()
            .parse_args(
                [
                    "import-missing",
                    "123",
                    "input.zip",
                    "--apply",
                    "--backup",
                    "before.json",
                    "--receipt",
                    "saved.json",
                ]
            )
            .apply
        )


if __name__ == "__main__":
    unittest.main()
