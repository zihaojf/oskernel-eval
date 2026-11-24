"""报告层测试：分组明细与证据提取。

Run with::

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starry_eval.evidence import point_evidence, split_point_blocks
from starry_eval.model import (
    ArchResult,
    GroupResult,
    STATUS_FAIL,
    STATUS_FULL,
    SuiteResult,
    TestPoint,
)
from starry_eval.report import build_group_details, render_markdown

BASIC_BODY = """========== START test_mount ==========
Mounting dev:/dev/vda2 to ./mnt
mount return: -19

 --- Assert Fatal ! ---
========== START test_munmap ==========
file len: 27
munmap return: 0
munmap successfully!
========== END test_munmap ==========
"""


def make_result() -> ArchResult:
    mount = TestPoint(
        name="test_mount",
        score=0.0,
        max_score=5.0,
        passed=0,
        total=5,
        status=STATUS_FAIL,
        note="0/5",
    )
    munmap = TestPoint(
        name="test_munmap",
        score=4.0,
        max_score=4.0,
        passed=4,
        total=4,
        status=STATUS_FULL,
        note="4/4",
    )
    group = GroupResult(
        group="basic-glibc",
        suite="basic",
        variant="glibc",
        complete=True,
        body=BASIC_BODY,
        points=[mount, munmap],
    )
    suite = SuiteResult(suite="basic", variant="glibc", groups=[group], ran=True)
    return ArchResult(
        arch="rv",
        label="test",
        groups=[group],
        suites=[suite],
    )


class PointBlockTest(unittest.TestCase):
    def test_split_marks_truncated_block(self) -> None:
        blocks = split_point_blocks(BASIC_BODY)
        self.assertFalse(blocks["test_mount"].terminated)
        self.assertTrue(blocks["test_munmap"].terminated)

    def test_evidence_prefers_failure_lines(self) -> None:
        lines, truncated = point_evidence(BASIC_BODY, "test_mount")
        self.assertIn("mount return: -19", lines)
        self.assertTrue(any("Assert Fatal" in line for line in lines))
        self.assertTrue(truncated)

    def test_evidence_falls_back_to_matching_lines(self) -> None:
        lines, truncated = point_evidence("hello test_mount world\n", "test_mount")
        self.assertEqual(lines, ["hello test_mount world"])
        self.assertFalse(truncated)


class GroupDetailTest(unittest.TestCase):
    def test_lost_points_carry_evidence(self) -> None:
        details = build_group_details(make_result())
        self.assertEqual(len(details), 1)
        group = details[0]
        self.assertAlmostEqual(group["lost"], 5.0)
        self.assertEqual(group["points_total"], 2)
        mount = next(p for p in group["points"] if p["name"] == "test_mount")
        self.assertEqual(mount["status_label"], "失败")
        self.assertIn("mount return: -19", mount["evidence"])
        self.assertTrue(mount["evidence_truncated"])

    def test_markdown_has_group_detail_section(self) -> None:
        report = {
            "arch": "rv",
            "label": "test",
            "summary": {
                "official": {
                    "score": 92,
                    "max_score": 102,
                    "verbatim": {"score": 92, "max_score": 102},
                    "intended": {"score": 92, "max_score": 102},
                },
                "composite": {"score": 92, "max_score": 102},
                "counts": {
                    "points_full": 1,
                    "points_partial": 0,
                    "points_failed": 1,
                    "points_skipped": 0,
                    "groups": 1,
                    "groups_complete": 1,
                    "groups_incomplete": 0,
                    "test_points": 2,
                },
                "suite_rows": [],
                "missing": [],
                "warnings": [],
                "lost_points": [],
                "lost_by_suite": [],
                "group_details": build_group_details(make_result()),
                "metrics": [],
            },
            "suites": [],
        }
        text = render_markdown(report)
        self.assertIn("## 分组明细", text)
        self.assertIn("basic-glibc", text)
        self.assertIn("test_mount", text)
        self.assertIn("mount return: -19", text)


if __name__ == "__main__":
    unittest.main()
