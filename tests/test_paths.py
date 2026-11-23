"""Path-resolution tests.

The point of these tests is the *distribution* contract: a fresh clone that
only has ``vendor/judge`` (no upstream ``oskernel-testsuits-cooperation``
checkout) must still find a judge directory, and an explicit ``--judge-dir``
must always win.

Run with::

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starry_eval import config


class JudgeResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sibling = self.root / "sibling" / "judge"
        self.vendor = self.root / "vendor" / "judge"
        self.starry = self.root / "StarryOS"
        self.starry.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _paths(self, **kwargs):
        with mock.patch.object(config, "SIBLING_JUDGE_DIR", self.sibling), \
             mock.patch.object(config, "VENDOR_JUDGE_DIR", self.vendor), \
             mock.patch.object(config, "WORKSPACE_ROOT", self.root), \
             mock.patch.object(config, "TOOL_ROOT", self.root / "starry-eval"):
            return config.default_paths(starry_dir=self.starry, **kwargs)

    def test_sibling_checkout_wins_when_present(self) -> None:
        self.sibling.mkdir(parents=True)
        self.vendor.mkdir(parents=True)
        self.assertEqual(self._paths().judge_dir, self.sibling.resolve())

    def test_vendor_used_when_no_sibling_checkout(self) -> None:
        self.vendor.mkdir(parents=True)
        self.assertEqual(self._paths().judge_dir, self.vendor.resolve())

    def test_explicit_judge_dir_wins_over_both(self) -> None:
        self.sibling.mkdir(parents=True)
        self.vendor.mkdir(parents=True)
        explicit = self.root / "my-judge"
        explicit.mkdir()
        self.assertEqual(
            self._paths(judge_dir=explicit).judge_dir, explicit.resolve()
        )

    def test_nothing_found_points_at_the_upstream_path(self) -> None:
        # No checkout and no vendor copy: the error message must name the path
        # a user can actually go and clone, not the vendored one.
        self.assertEqual(self._paths().judge_dir, self.sibling.resolve())

    def test_kernel_and_image_follow_starry_dir(self) -> None:
        self.vendor.mkdir(parents=True)
        paths = self._paths()
        self.assertEqual(paths.kernel("rv"), (self.starry / "kernel-rv").resolve())
        self.assertEqual(
            paths.image("la"), (self.starry / "sdcard-la.img").resolve()
        )


class DropInLayoutTest(unittest.TestCase):
    """A teammate may have no OS checkout at all -- only this repo + images."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "oskernel-eval"
        (self.repo / "vendor" / "judge").mkdir(parents=True)
        self.paths = config.Paths(
            starry_dir=self.root / "StarryOS",       # does not exist
            judge_dir=self.repo / "vendor" / "judge",
            runs_dir=self.repo / "runs",
            baseline_file=self.repo / "baseline.json",
            tool_root=self.repo,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_kernel_falls_back_to_kernels_dir(self) -> None:
        (self.repo / "kernels").mkdir()
        k = self.repo / "kernels" / "kernel-rv"
        k.write_bytes(b"\x7fELF")
        self.assertEqual(self.paths.kernel("rv"), k)

    def test_image_falls_back_to_images_dir(self) -> None:
        (self.repo / "images").mkdir()
        img = self.repo / "images" / "sdcard-la.img"
        img.write_bytes(b"")
        self.assertEqual(self.paths.image("la"), img)

    def test_starry_dir_wins_over_drop_in(self) -> None:
        (self.repo / "kernels").mkdir()
        (self.repo / "kernels" / "kernel-rv").write_bytes(b"dropin")
        self.paths.starry_dir.mkdir()
        built = self.paths.starry_dir / "kernel-rv"
        built.write_bytes(b"built")
        self.assertEqual(self.paths.kernel("rv"), built)

    def test_missing_everything_names_the_starry_path(self) -> None:
        # The "not found" message must point at the thing the user can create.
        self.assertEqual(
            self.paths.kernel("rv"), self.paths.starry_dir / "kernel-rv"
        )
        self.assertEqual(
            self.paths.image("rv"), self.paths.starry_dir / "sdcard-rv.img"
        )


class DefaultStarryDirTest(unittest.TestCase):
    """Both repo names must be recognised, and a submodule layout too."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "work"
        self.tool = self.root / "work" / "oskernel-eval"
        self.workspace.mkdir()
        self.tool.mkdir()
        (self.tool / "vendor" / "judge").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _team_repo(self, path: Path) -> Path:
        (path / "src").mkdir(parents=True)
        (path / "Makefile").write_text("all:\n", encoding="utf-8")
        return path

    def _resolve(self) -> Path:
        with mock.patch.object(config, "WORKSPACE_ROOT", self.workspace), \
             mock.patch.object(config, "TOOL_ROOT", self.tool):
            return config.default_paths().starry_dir

    def test_oops_starry_os_is_recognised(self) -> None:
        expected = self._team_repo(self.workspace / "Oops-StarryOS")
        self.assertEqual(self._resolve(), expected.resolve())

    def test_starry_os_name_still_wins_when_both_exist(self) -> None:
        expected = self._team_repo(self.workspace / "StarryOS")
        self._team_repo(self.workspace / "Oops-StarryOS")
        self.assertEqual(self._resolve(), expected.resolve())

    def test_submodule_inside_the_eval_repo_is_found(self) -> None:
        expected = self._team_repo(self.tool / "StarryOS")
        self.assertEqual(self._resolve(), expected.resolve())

    def test_falls_back_to_the_conventional_path(self) -> None:
        self.assertEqual(
            self._resolve(), (self.workspace / "StarryOS").resolve()
        )


if __name__ == "__main__":
    unittest.main()
