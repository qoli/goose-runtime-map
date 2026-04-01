import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import grm


class GrmTests(unittest.TestCase):
    def test_parse_goose_info_config_path(self) -> None:
        info_text = """goose Version:
  Version: 1.29.0

Paths:
Config dir: /Users/test/.config/goose
Config yaml: /Users/test/.config/goose/config.yaml
"""
        self.assertEqual(
            grm.parse_goose_info_config_path(info_text),
            Path("/Users/test/.config/goose/config.yaml"),
        )

    def test_parse_goose_provider(self) -> None:
        config_text = """
extensions:
  analyze:
    enabled: true
GOOSE_PROVIDER: "custom_omlx"
GOOSE_MODEL: something
"""
        self.assertEqual(grm.parse_goose_provider(config_text), "custom_omlx")

    def test_build_goose_command_without_override(self) -> None:
        with mock.patch("grm.shutil.which", return_value="/opt/homebrew/bin/goose"):
            command = grm.build_goose_command("goose")
        self.assertEqual(command, ["goose", "run", "--instructions", "-", "--no-session", "--quiet"])

    def test_build_goose_command_with_override(self) -> None:
        override = grm.ModelOverride(
            model="mlx-model",
            provider="custom_omlx",
            config_path=Path("/tmp/config.yaml"),
        )
        with mock.patch("grm.shutil.which", return_value="/opt/homebrew/bin/goose"):
            command = grm.build_goose_command("goose", override)
        self.assertEqual(
            command,
            [
                "goose",
                "run",
                "--instructions",
                "-",
                "--no-session",
                "--quiet",
                "--provider",
                "custom_omlx",
                "--model",
                "mlx-model",
            ],
        )

    def test_resolve_model_override_requires_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("GOOSE_MODEL: existing-model\n", encoding="utf-8")
            with mock.patch("grm.read_goose_config_path", return_value=config_path):
                with self.assertRaisesRegex(ValueError, "unable to resolve GOOSE_PROVIDER"):
                    grm.resolve_model_override("goose", "mlx-model")

    def test_write_history_includes_override_metadata(self) -> None:
        override = grm.ModelOverride(
            model="mlx-model",
            provider="custom_omlx",
            config_path=Path("/tmp/config.yaml"),
        )
        validation = grm.ValidationResult(
            passed=False,
            ratio=0.0,
            matched_sections=0,
            total_sections=10,
            reasons=["skeleton_ratio_below_threshold_0.00"],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            previous_cwd = Path.cwd()
            os.chdir(tmpdir)
            try:
                history_path = grm.write_history(
                    question="test question",
                    answer="A. test",
                    stderr="",
                    exit_code=0,
                    duration=1.0,
                    attempts_used=1,
                    validation=validation,
                    command=[
                        "goose",
                        "run",
                        "--instructions",
                        "-",
                        "--no-session",
                        "--quiet",
                        "--provider",
                        "custom_omlx",
                        "--model",
                        "mlx-model",
                    ],
                    model_override=override,
                )
            finally:
                os.chdir(previous_cwd)

            content = history_path.read_text(encoding="utf-8")
            self.assertIn("- model_override: mlx-model", content)
            self.assertIn("- provider_from_config: custom_omlx", content)
            self.assertIn("- provider_config_path: /tmp/config.yaml", content)
            self.assertIn("--provider custom_omlx --model mlx-model", content)

    def test_write_history_omits_override_metadata_when_unused(self) -> None:
        validation = grm.ValidationResult(
            passed=True,
            ratio=1.0,
            matched_sections=10,
            total_sections=10,
            reasons=[],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            previous_cwd = Path.cwd()
            os.chdir(tmpdir)
            try:
                history_path = grm.write_history(
                    question="test question",
                    answer="A. test",
                    stderr="",
                    exit_code=0,
                    duration=1.0,
                    attempts_used=1,
                    validation=validation,
                    command=["goose", "run", "--instructions", "-", "--no-session", "--quiet"],
                )
            finally:
                os.chdir(previous_cwd)

            content = history_path.read_text(encoding="utf-8")
            self.assertNotIn("model_override", content)
            self.assertNotIn("provider_from_config", content)


if __name__ == "__main__":
    unittest.main()
