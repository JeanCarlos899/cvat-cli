import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as N
import unittest

from cvat_cli import ensure_empty, parser, read_env, resolve_annotations, resources
from sampling import uniform_indices


class CliTests(unittest.TestCase):
    def test_uniform(self):
        indices = uniform_indices(9090, 200)
        self.assertEqual(len(set(indices)), 200)
        self.assertEqual((indices[0], indices[-1]), (0, 9089))
        gaps = [b - a for a, b in zip(indices, indices[1:])]
        self.assertLessEqual(max(gaps) - min(gaps), 1)

    def test_sampling_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            uniform_indices(3, 4)
        self.assertEqual(uniform_indices(100, 1), [0])

    def test_env_permissions(self):
        with TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text('CVAT_USERNAME=test-user\nCVAT_PASSWORD="not-real"\n')
            path.chmod(0o600)
            self.assertEqual(read_env(path)["CVAT_PASSWORD"], "not-real")
            if os.name == "posix":
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    read_env(path)

    def test_directory_ignores_metadata(self):
        with TemporaryDirectory() as d:
            for name in ["frame_000002.jpg", "manifest.json", "frame_000001.jpg"]:
                (Path(d) / name).touch()
            self.assertEqual(
                [Path(p).name for p in resources([d])],
                ["frame_000001.jpg", "frame_000002.jpg"],
            )
            with self.assertRaises(ValueError):
                resources([d, d])

    def test_label_attribute_mapping(self):
        labels = [
            N(
                name="pessoa",
                id=986,
                attributes=[
                    N(
                        name="atividade",
                        id=53,
                        input_type="select",
                        values=["indeterminado"],
                    )
                ],
            )
        ]
        payload = {
            "shapes": [
                dict(
                    label="pessoa",
                    type="rectangle",
                    points=[1, 2, 30, 40],
                    frame=0,
                    attributes={"atividade": "indeterminado"},
                )
            ]
        }
        result = resolve_annotations(payload, labels, 200)
        self.assertEqual(result["shapes"][0]["label_id"], 986)
        self.assertEqual(result["shapes"][0]["attributes"][0]["spec_id"], 53)
        self.assertIn("label", payload["shapes"][0])
        with self.assertRaises(ValueError):
            resolve_annotations(payload, labels, 0)
        payload["shapes"][0]["attributes"]["atividade"] = "inventado"
        with self.assertRaises(ValueError):
            resolve_annotations(payload, labels, 200)

    def test_existing_annotation_guard(self):
        task = N(get_annotations=lambda: N(shapes=[1], tags=[], tracks=[]))
        with self.assertRaises(ValueError):
            ensure_empty(task)

    def test_command_parser(self):
        args = parser().parse_args(
            [
                "task-create",
                "Amostras",
                "--project",
                "150",
                "--files",
                "video.mp4",
                "--step",
                "45",
            ]
        )
        self.assertEqual(args.step, 45)
        self.assertEqual(args.project, 150)

    def test_command_reference_covers_every_subcommand(self):
        choices = next(
            action.choices
            for action in parser()._actions
            if hasattr(action, "choices") and action.choices
        )
        reference = (Path(__file__).parent / "docs" / "CLI.md").read_text()
        for command in choices:
            with self.subTest(command=command):
                self.assertIn(f"`{command}", reference)
        forbidden_brand = "get" + "ter"
        self.assertNotIn(forbidden_brand, reference.casefold())


if __name__ == "__main__":
    unittest.main()
