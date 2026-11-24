"""人类可读 / 机器可读的报告。

``build_report`` 产出的同一份结构会喂给 JSON、Markdown 和终端三个渲染器；
本模块不改分，只格式化已经在内存里的 ``ArchResult``。报告正文使用中文，
JSON 的键保持英文，方便脚本继续消费。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .evidence import point_evidence, split_point_blocks
from .history import (
    REPORT_JSON,
    REPORT_MD,
    composite_totals,
    group_counted,
    official_totals,
    summary_entry,
)
from .model import (
    ArchResult,
    GroupResult,
    Metric,
    STATUS_FAIL,
    STATUS_FULL,
    STATUS_PARTIAL,
    STATUS_SKIP,
    SuiteResult,
    TestPoint,
)
from .references import (
    COMPOSITE_SUITES,
    OFFICIAL_SUITES,
    SUITE_ORDER,
    SUITE_SPECS,
)

VARIANT_ORDER = {"glibc": 0, "musl": 1, "default": 2}

# 状态 -> 中文标签。报告里只显示中文，JSON 里仍是英文常量。
POINT_STATUS_LABELS = {
    STATUS_FULL: "通过",
    STATUS_PARTIAL: "部分",
    STATUS_FAIL: "失败",
    STATUS_SKIP: "跳过",
}

SUITE_STATUS_LABELS = {
    "ok": "正常",
    "not-run": "未运行",
    "incomplete": "未完成",
    "script-no-group": "无分组标记",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _num(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.{digits}f}"


def _pct(score: float, max_score: float) -> str:
    if max_score <= 0:
        return "-"
    return f"{score / max_score * 100:.1f}%"


def _duration(value: float | None) -> str:
    if value is None or value <= 0:
        return "-"
    if value < 60:
        return f"{value:.1f}s"
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes}m{seconds:.0f}s"


def _suite_key(suite: str, variant: str) -> str:
    return f"{suite}-{variant}" if variant != "default" else suite


def _suite_title(suite: str) -> str:
    spec = SUITE_SPECS.get(suite)
    return spec.title if spec is not None else suite


def _sort_key(suite: SuiteResult) -> tuple[int, int, str]:
    try:
        order = SUITE_ORDER.index(suite.suite)
    except ValueError:
        order = len(SUITE_ORDER)
    return (order, VARIANT_ORDER.get(suite.variant, 9), suite.variant)


def _point_status_label(point: TestPoint) -> str:
    return POINT_STATUS_LABELS.get(point.status, point.status)


def _status_label(status: str) -> str:
    return SUITE_STATUS_LABELS.get(status, status)


def _md_cell(text: Any) -> str:
    """Markdown 表格单元：转义竖线，压成一行。"""

    value = str(text if text is not None else "")
    value = value.replace("|", "\\|").replace("\n", " ")
    return " ".join(value.split())


# ---------------------------------------------------------------------------
# 套件行
# ---------------------------------------------------------------------------


def suite_counted(suite: SuiteResult) -> tuple[float, float]:
    score = 0.0
    max_score = 0.0
    for group in suite.groups:
        group_score, group_max = group_counted(group)
        score += group_score
        max_score += group_max
    return score, max_score


def _suite_status(suite: SuiteResult) -> str:
    if not suite.groups:
        return "not-run"
    if any(g.source == "not-run" for g in suite.groups):
        return "not-run"
    if any(g.source == "script-no-group" for g in suite.groups):
        return "script-no-group"
    if not suite.complete:
        return "incomplete"
    return "ok"


def build_suite_rows(result: ArchResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        score, max_score = suite_counted(suite)
        alternatives = [
            (g.alternate_label, g.alternate_score, g.alternate_max)
            for g in suite.groups
            if g.alternate_score is not None
        ]
        row = {
            "suite": suite.suite,
            "title": _suite_title(suite.suite),
            "variant": suite.variant,
            "name": _suite_key(suite.suite, suite.variant),
            "ran": suite.ran,
            "complete": suite.complete,
            "status": _suite_status(suite),
            "status_label": _status_label(_suite_status(suite)),
            "score": round(score, 6),
            "max_score": round(max_score, 6),
            "raw_score": round(suite.score, 6),
            "raw_max": round(suite.max_score, 6),
            "points_total": suite.points_total,
            "points_full": suite.points_full,
            "points_partial": suite.points_partial,
            "points_failed": suite.points_failed,
            "points_skipped": suite.points_skipped,
            "duration_sec": (
                round(suite.duration_sec, 3)
                if suite.duration_sec is not None
                else None
            ),
            "groups": [g.group for g in suite.groups],
            "warnings": suite.warnings,
        }
        if alternatives:
            label, alt_score, alt_max = alternatives[0]
            if alt_score is not None:
                row["alternate"] = {
                    "label": label,
                    "score": round(alt_score, 6),
                    "max_score": round(alt_max or 0.0, 6),
                }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 未运行 / 警告 / 失分
# ---------------------------------------------------------------------------


def build_missing(result: ArchResult) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        status = _suite_status(suite)
        if status == "ok":
            continue
        score, max_score = suite_counted(suite)
        if status == "not-run":
            reason = "操作系统没有运行这个套件（没有 START/END 标记）"
        elif status == "script-no-group":
            reason = "测试脚本运行了，但没有打印 START/END 分组标记"
        else:
            reason = "已经开始但没有跑完（被终止或中途退出）"
        missing.append(
            {
                "name": _suite_key(suite.suite, suite.variant),
                "title": _suite_title(suite.suite),
                "suite": suite.suite,
                "variant": suite.variant,
                "status": status,
                "status_label": _status_label(status),
                "score": round(score, 6),
                "max_score": round(max_score, 6),
                "reason": reason,
            }
        )
    return missing


def build_warnings(result: ArchResult) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            warnings.append(text)

    for warning in result.warnings:
        add(warning)
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            if group.source == "not-run":
                continue
            for warning in group.warnings:
                add(f"{group.group}：{warning}")
    return warnings


def build_lost_by_suite(result: ArchResult) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            if group.source in ("not-run", "script-no-group"):
                continue
            key = _suite_key(suite.suite, suite.variant)
            bucket = buckets.setdefault(
                key,
                {
                    "name": key,
                    "title": _suite_title(suite.suite),
                    "suite": suite.suite,
                    "variant": suite.variant,
                    "lost": 0.0,
                    "points": 0,
                },
            )
            for point in group.points:
                if point.status == STATUS_SKIP or point.lost <= 1e-9:
                    continue
                bucket["lost"] += point.lost
                bucket["points"] += 1
    rows = [row for row in buckets.values() if row["lost"] > 1e-9]
    rows.sort(key=lambda row: row["lost"], reverse=True)
    for row in rows:
        row["lost"] = round(row["lost"], 6)
    return rows


def build_lost_points(
    result: ArchResult, *, limit: int = 30
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            if group.source in ("not-run", "script-no-group"):
                continue
            for point in group.points:
                if point.status == STATUS_SKIP or point.lost <= 1e-9:
                    continue
                rows.append(
                    {
                        "group": group.group,
                        "suite": suite.suite,
                        "title": _suite_title(suite.suite),
                        "variant": suite.variant,
                        "name": point.name,
                        "status": point.status,
                        "status_label": _point_status_label(point),
                        "score": round(point.score, 6),
                        "max_score": round(point.max_score, 6),
                        "lost": round(point.lost, 6),
                        "note": point.note,
                    }
                )
    rows.sort(key=lambda row: row["lost"], reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# 分组明细：分组内部每个测试点的得分与证据
# ---------------------------------------------------------------------------


def build_group_details(result: ArchResult) -> list[dict[str, Any]]:
    """把每个已运行的分组拆到测试点级别，并附上失分证据。

    一个分组拿到 92/102 却看不出差在哪里，是这份报告要解决的问题：这里给出
    组内每个测试点的得分、通过数/总数、状态，以及从串口输出里切出来的证据行。
    """

    rows: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            if group.source == "not-run":
                continue
            blocks = split_point_blocks(group.body)
            points: list[dict[str, Any]] = []
            for point in group.points:
                evidence, truncated = point_evidence(
                    group.body, point.name, blocks=blocks
                )
                assertions = [
                    str(item) for item in point.extra.get("failed_assertions", [])
                ]
                points.append(
                    {
                        "name": point.name,
                        "kind": point.kind,
                        "status": point.status,
                        "status_label": _point_status_label(point),
                        "score": round(point.score, 6),
                        "max_score": round(point.max_score, 6),
                        "lost": round(point.lost, 6),
                        "passed": point.passed,
                        "total": point.total,
                        "note": point.note,
                        "evidence": evidence,
                        "evidence_truncated": truncated,
                        "failed_assertions": assertions,
                        # 串口里连这一段的输出都没有：judge 只能判 0 分。
                        "no_output": (
                            not evidence
                            and not assertions
                            and point.status == STATUS_FAIL
                        ),
                    }
                )
            lost = sum(item["lost"] for item in points)
            rows.append(
                {
                    "name": _suite_key(suite.suite, suite.variant),
                    "title": _suite_title(suite.suite),
                    "suite": suite.suite,
                    "variant": suite.variant,
                    "group": group.group,
                    "complete": group.complete,
                    "score": round(group.score, 6),
                    "max_score": round(group.max_score, 6),
                    "lost": round(lost, 6),
                    "points_total": group.points_total,
                    "points_full": group.points_full,
                    "points_partial": group.points_partial,
                    "points_failed": group.points_failed,
                    "points_skipped": group.points_skipped,
                    "duration_sec": (
                        round(group.duration_sec, 3)
                        if group.duration_sec is not None
                        else None
                    ),
                    "points": points,
                }
            )
    return rows


def _imperfect_points(group: dict[str, Any]) -> list[dict[str, Any]]:
    """分组内没有拿到满分的测试点（含被判为失败的 0 分点）。"""

    return [
        point
        for point in group.get("points", [])
        if point["lost"] > 1e-9 or point["status"] == STATUS_FAIL
    ]


def _explained_points(group: dict[str, Any]) -> list[dict[str, Any]]:
    """有线索的未满分点（串口里有输出或 judge 给了断言）。"""

    return [point for point in _imperfect_points(group) if not point.get("no_output")]


def _silent_points(group: dict[str, Any]) -> list[dict[str, Any]]:
    """串口里完全没有输出、judge 直接判 0 的测试点。"""

    return [point for point in _imperfect_points(group) if point.get("no_output")]


def _name_list(points: list[dict[str, Any]], limit: int = 12) -> str:
    names = [str(point["name"]) for point in points[:limit]]
    text = "、".join(names)
    if len(points) > limit:
        text += f" 等 {len(points)} 个"
    return text


def _point_evidence_lines(point: dict[str, Any], limit: int = 3) -> list[str]:
    """串口输出里的线索，外加 judge 断言级别的线索（两者互补）。"""

    lines = [str(item) for item in point.get("evidence", [])][:limit]
    for text in point.get("failed_assertions", [])[:2]:
        lines.append(f"断言：{text}")
    return lines


def _note_text(point: dict[str, Any]) -> str:
    """``3/3`` 这种纯计数说明和「通过/总数」列重复，直接不显示。"""

    note = str(point.get("note") or "")
    match = re.fullmatch(r"\d+\s*/\s*\d+", note.strip())
    return "" if match else note


def _count_text(point: dict[str, Any]) -> str:
    if point.get("total"):
        return f"{point.get('passed') or 0}/{point.get('total')}"
    return "-"


# ---------------------------------------------------------------------------
# 性能指标
# ---------------------------------------------------------------------------


def build_metrics(result: ArchResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            for metric in group.metrics:
                rows.append(
                    {
                        "group": group.group,
                        "suite": suite.suite,
                        "variant": suite.variant,
                        "name": metric.name,
                        "value": metric.value,
                        "unit": metric.unit,
                        "direction": metric.direction,
                        "reference": metric.reference,
                        "reference_kind": metric.reference_kind,
                        "range_low": metric.range_low,
                        "range_high": metric.range_high,
                        "quality": metric.quality,
                        "delta_pct": metric.delta_pct,
                        "note": metric.note,
                    }
                )
    return rows


def _metric_quality_summary(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = [m["quality"] for m in metrics if m.get("quality") is not None]
    if not values:
        return {"count": 0, "mean_quality": None}
    return {
        "count": len(values),
        "mean_quality": sum(values) / len(values),
        "min_quality": min(values),
        "max_quality": max(values),
    }


# ---------------------------------------------------------------------------
# 汇总结构
# ---------------------------------------------------------------------------


def build_summary(
    result: ArchResult, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    official_score, official_max = official_totals(result)
    alternate_score, alternate_max = official_totals(result, alternate=True)
    if result.score_mode == "raw":
        verbatim = (official_score, official_max)
        intended = (alternate_score, alternate_max)
    else:
        intended = (official_score, official_max)
        verbatim = (alternate_score, alternate_max)
    composite_score, composite_max = composite_totals(result)
    suite_rows = build_suite_rows(result)

    previous_suites: dict[str, dict[str, Any]] = {}
    if previous:
        for row in previous.get("suites", []):
            previous_suites[f"{row.get('suite')}-{row.get('variant')}"] = row

    for row in suite_rows:
        prev = previous_suites.get(f"{row['suite']}-{row['variant']}")
        if prev is not None:
            row["previous_score"] = prev.get("score")
            row["delta"] = round(row["score"] - float(prev.get("score", 0.0)), 6)
        else:
            row["previous_score"] = None
            row["delta"] = None

    metrics = build_metrics(result)
    by_variant: dict[str, dict[str, float]] = {}
    for suite in result.suites:
        score, max_score = suite_counted(suite)
        bucket = by_variant.setdefault(
            suite.variant, {"score": 0.0, "max_score": 0.0}
        )
        bucket["score"] += score
        bucket["max_score"] += max_score

    counts = {
        "groups": len(result.groups),
        "groups_complete": len([g for g in result.groups if g.complete]),
        "groups_incomplete": len([g for g in result.groups if not g.complete]),
        "test_points": sum(s.points_total for s in result.suites),
        "points_full": sum(s.points_full for s in result.suites),
        "points_partial": sum(s.points_partial for s in result.suites),
        "points_failed": sum(s.points_failed for s in result.suites),
        "points_skipped": sum(s.points_skipped for s in result.suites),
    }

    return {
        "official": {
            "score": round(official_score, 6),
            "max_score": round(official_max, 6),
            "verbatim": {
                "score": round(verbatim[0], 6),
                "max_score": round(verbatim[1], 6),
            },
            "intended": {
                "score": round(intended[0], 6),
                "max_score": round(intended[1], 6),
            },
            "suites": list(OFFICIAL_SUITES),
        },
        "composite": {
            "score": round(composite_score, 6),
            "max_score": round(composite_max, 6),
            "suites": list(COMPOSITE_SUITES),
            "note": "本地综合分；ltp 分母随镜像变化，不计入",
        },
        "by_variant": {
            key: {
                "score": round(value["score"], 6),
                "max_score": round(value["max_score"], 6),
            }
            for key, value in sorted(by_variant.items())
        },
        "counts": counts,
        "suite_rows": suite_rows,
        "missing": build_missing(result),
        "warnings": build_warnings(result),
        "lost_points": build_lost_points(result),
        "lost_by_suite": build_lost_by_suite(result),
        "group_details": build_group_details(result),
        "metrics": metrics,
        "metric_summary": _metric_quality_summary(metrics),
        "scripts": result.scripts,
    }


# ---------------------------------------------------------------------------
# 对外结构
# ---------------------------------------------------------------------------


def build_report(
    result: ArchResult,
    run_dir: Path,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = result.to_dict()
    data["run_dir"] = str(run_dir)
    data["summary"] = build_summary(result, previous)
    data["previous"] = (
        {
            "run_dir": previous.get("run_dir"),
            "started_at": previous.get("started_at"),
            "official_score": previous.get("official_score"),
            "official_max": previous.get("official_max"),
        }
        if previous
        else None
    )
    data["index_entry"] = summary_entry(result, run_dir)
    return data


# ---------------------------------------------------------------------------
# 终端渲染
# ---------------------------------------------------------------------------


def render_text(report: dict[str, Any]) -> str:
    meta = report
    summary = report["summary"]
    lines: list[str] = []

    arch = meta.get("arch", "?")
    label = meta.get("label", "")
    lines.append("=" * 78)
    lines.append(f"StarryOS 本地评测  -  {arch}（{label}）")
    lines.append("=" * 78)
    lines.append(f"内核     : {meta.get('kernel') or '-'}")
    lines.append(f"镜像     : {meta.get('image') or '-'}")
    lines.append(
        f"运行     : {meta.get('started_at') or '-'}   "
        f"用时 {_duration(meta.get('wall_time_sec'))}   "
        f"退出码 {meta.get('exit_code')}   "
        f"超时 {'是' if meta.get('timed_out') else '否'}"
    )
    lines.append(f"评分模式 : {meta.get('score_mode')}")
    lines.append("")

    official = summary["official"]
    composite = summary["composite"]
    counts = summary["counts"]
    lines.append("总分")
    lines.append("-" * 78)
    verbatim = official.get("verbatim", official)
    intended = official.get("intended", official)
    if (
        abs(verbatim["score"] - intended["score"]) < 1e-9
        and abs(verbatim["max_score"] - intended["max_score"]) < 1e-9
    ):
        lines.append(
            f"官方 judge 得分      : {_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}"
            f"  ({_pct(verbatim['score'], verbatim['max_score'])})"
        )
    else:
        lines.append(
            f"官方 judge（逐字）   : {_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}"
            f"  ({_pct(verbatim['score'], verbatim['max_score'])})"
        )
        lines.append(
            f"官方 judge（修正换行）: {_num(intended['score'])} / "
            f"{_num(intended['max_score'])}"
            f"  ({_pct(intended['score'], intended['max_score'])})"
        )
    lines.append(
        f"本地综合分           : {_num(composite['score'])} / "
        f"{_num(composite['max_score'])}"
        f"  ({_pct(composite['score'], composite['max_score'])})"
    )
    lines.append(
        f"测试点               : {counts['points_full']} 通过，"
        f"{counts['points_partial']} 部分，{counts['points_failed']} 失败，"
        f"{counts['points_skipped']} 跳过"
    )
    lines.append(
        f"分组                 : {counts['groups_complete']}/{counts['groups']} 完整"
    )
    lines.append("")

    lines.append("套件得分")
    lines.append("-" * 78)
    header = (
        f"{'套件':<18} {'变体':<7} {'得分/满分':>17} {'全/半/失':>12} "
        f"{'用时':>8}  {'状态':<10} 变化"
    )
    lines.append(header)
    for row in summary["suite_rows"]:
        delta = row.get("delta")
        delta_text = f"{delta:+.1f}" if delta is not None else ""
        lines.append(
            f"{row['suite']:<18} {row['variant']:<7} "
            f"{_num(row['score']):>7}/{_num(row['max_score']):<9} "
            f"{row['points_full']:>4}/{row['points_partial']:>3}/{row['points_failed']:<4} "
            f"{_duration(row['duration_sec']):>8}  "
            f"{row['status_label']:<10} {delta_text}"
        )
    for row in summary["suite_rows"]:
        alternate = row.get("alternate")
        if alternate:
            lines.append(
                f"  注：{row['name']}：{alternate['label']} "
                f"{_num(alternate['score'])}/{_num(alternate['max_score'])}"
            )
        if row["status"] in ("incomplete", "script-no-group") and row["raw_score"] > 0:
            lines.append(
                f"  注：{row['name']}：只跑出一部分原始分 "
                f"{_num(row['raw_score'])}/{_num(row['raw_max'])}（未计入总分）"
            )
    lines.append("")

    missing = summary["missing"]
    if missing:
        lines.append("未运行 / 未完成")
        lines.append("-" * 78)
        for row in missing:
            lines.append(
                f"! {row['name']:<24} {_num(row['score']):>6}/{_num(row['max_score']):<6} "
                f"{row['reason']}"
            )
        lines.append("")

    lost_by_suite = summary.get("lost_by_suite", [])
    if lost_by_suite:
        lines.append("各套件失分（从多到少）")
        lines.append("-" * 78)
        for row in lost_by_suite[:12]:
            lines.append(
                f"  {row['name']:<24} 失 {row['lost']:>7.1f} "
                f"分，涉及 {row['points']} 个测试点"
            )
        lines.append("")

    # 组内明细：这是「分组有分但不知道差在哪」的直接答案。
    details = summary.get("group_details", [])
    imperfect_groups = [g for g in details if g["lost"] > 1e-9]
    if imperfect_groups:
        lines.append("分组明细（未满分的测试点）")
        lines.append("-" * 78)
        for group in imperfect_groups:
            lines.append(
                f"[{group['name']}] {group['title']}  "
                f"{_num(group['score'])}/{_num(group['max_score'])}"
                f"  失 {_num(group['lost'])}  测试点 {group['points_total']} "
                f"（通过 {group['points_full']} / 部分 {group['points_partial']} "
                f"/ 失败 {group['points_failed']} / 跳过 {group['points_skipped']}）"
                f"{'' if group['complete'] else '  [分组不完整]'}"
            )
            explained = _explained_points(group)
            for index, point in enumerate(explained[:20], 1):
                lines.append(
                    f"  {index:>2}. {point['name']:<28} "
                    f"{_num(point['score']):>4}/{_num(point['max_score']):<4}"
                    f"  通过 {_count_text(point):<7} {point['status_label']}"
                )
                evidence = _point_evidence_lines(point)
                if evidence:
                    lines.append(f"      证据：{' | '.join(evidence)}")
                if point.get("evidence_truncated"):
                    lines.append("      （没有 END 标记：输出被下一段截断）")
            if len(explained) > 20:
                lines.append(
                    f"  …… 另有 {len(explained) - 20} 个未满分测试点，见 report.md"
                )
            silent = _silent_points(group)
            if silent:
                lines.append(
                    f"  * 另有 {len(silent)} 个测试点在串口里完全没有输出"
                    f"（judge 直接判 0）：{_name_list(silent)}"
                )
            lines.append("")

    warnings = summary["warnings"]
    if warnings:
        lines.append("警告")
        lines.append("-" * 78)
        for warning in warnings[:30]:
            lines.append(f"! {warning}")
        if len(warnings) > 30:
            lines.append(f"! …… 另有 {len(warnings) - 30} 条，见 report.json")
        lines.append("")

    lines.append("性能指标")
    lines.append("-" * 78)
    metrics_by_group: dict[str, list[dict[str, Any]]] = {}
    for metric in summary["metrics"]:
        metrics_by_group.setdefault(metric["group"], []).append(metric)
    for group_name in sorted(metrics_by_group, key=_group_order):
        metrics = metrics_by_group[group_name]
        if not metrics:
            continue
        suite = metrics[0]["suite"]
        variant = metrics[0]["variant"]
        lines.append(f"[{_suite_key(suite, variant)}] {len(metrics)} 项指标")
        # 有质量分时最差的排前面，否则按输出顺序。
        def rank_key(metric: dict[str, Any]) -> tuple[int, float, float]:
            quality = metric.get("quality")
            value = float(metric.get("value", 0.0))
            # "lower" 表示越小越好，数值大反而更差，所以取负。
            value_rank = -value if metric.get("direction") == "lower" else value
            if quality is not None:
                return (0, float(quality), value_rank)
            return (1, 0.0, value_rank)

        ranked = sorted(metrics, key=rank_key)
        for metric in ranked[:12]:
            value = f"{_num(metric['value'], 3)} {metric['unit']}".strip()
            extra = ""
            if metric.get("quality") is not None:
                extra += f" 质量={metric['quality']:.2f}"
            if metric.get("delta_pct") is not None:
                extra += f" 变化={metric['delta_pct']:+.1f}%"
            if metric.get("range_low") is not None:
                extra += (
                    f" 期望 {_num(metric['range_low'], 4)}"
                    f"-{_num(metric['range_high'], 4)} {metric['unit']}"
                )
            lines.append(f"  {metric['name']:<48} {value:<20}{extra}")
        if len(ranked) > 12:
            lines.append(f"  …… 另有 {len(ranked) - 12} 项")
        lines.append("")

    lines.append(
        "说明：官方 judge = 提供的 judge_*.py（逐字 / 修正换行两种口径）；"
        "本地综合分 = 除 ltp 之外所有套件的本地指数（不等于线上总分，"
        "对齐线上请用 compare-online）；全/半/失 = 通过/部分/失败。"
    )
    return "\n".join(lines) + "\n"


def _group_order(group_name: str) -> tuple[int, str]:
    suite = group_name.split("-")[0]
    try:
        return (SUITE_ORDER.index(suite), group_name)
    except ValueError:
        return (len(SUITE_ORDER), group_name)


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines: list[str] = []
    arch = report.get("arch", "?")
    label = report.get("label", "")

    lines.append(f"# StarryOS 本地评测 - {arch}（{label}）")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("|---|---|")
    for key, value in (
        ("内核", report.get("kernel")),
        ("镜像", report.get("image")),
        ("开始时间", report.get("started_at")),
        ("运行耗时（秒）", report.get("wall_time_sec")),
        ("退出码", report.get("exit_code")),
        ("是否超时", report.get("timed_out")),
        ("评分模式", report.get("score_mode")),
    ):
        lines.append(f"| {key} | {_md_cell(value)} |")
    lines.append("")

    official = summary["official"]
    composite = summary["composite"]
    counts = summary["counts"]
    lines.append("## 总览")
    lines.append("")
    verbatim = official.get("verbatim", official)
    intended = official.get("intended", official)
    if (
        abs(verbatim["score"] - intended["score"]) < 1e-9
        and abs(verbatim["max_score"] - intended["max_score"]) < 1e-9
    ):
        lines.append(
            f"- 官方 judge 得分：**{_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}** "
            f"({_pct(verbatim['score'], verbatim['max_score'])})"
        )
    else:
        lines.append(
            f"- 官方 judge（逐字）：**{_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}** "
            f"({_pct(verbatim['score'], verbatim['max_score'])})"
        )
        lines.append(
            f"- 官方 judge（修正换行）：**{_num(intended['score'])} / "
            f"{_num(intended['max_score'])}** "
            f"({_pct(intended['score'], intended['max_score'])})"
        )
    lines.append(
        f"- 本地综合分：**{_num(composite['score'])} / "
        f"{_num(composite['max_score'])}** "
        f"({_pct(composite['score'], composite['max_score'])})"
    )
    lines.append(
        f"- 测试点：{counts['points_full']} 通过，"
        f"{counts['points_partial']} 部分，{counts['points_failed']} 失败，"
        f"{counts['points_skipped']} 跳过"
    )
    lines.append(
        f"- 分组：{counts['groups_complete']}/{counts['groups']} 完整"
    )
    lines.append("")

    lines.append("## 套件得分")
    lines.append("")
    lines.append(
        "| 套件 | 说明 | 变体 | 得分 / 满分 | 通过/部分/失败 | 用时 | 状态 | 变化 | 备注 |"
    )
    lines.append("|---|---|---|---:|---:|---:|---|---:|---|")
    for row in summary["suite_rows"]:
        note = ""
        if row.get("alternate"):
            alt = row["alternate"]
            note = (
                f"{alt['label']}：{_num(alt['score'])}/{_num(alt['max_score'])}"
            )
        delta = row.get("delta")
        lines.append(
            f"| {row['suite']} | {_md_cell(row.get('title'))} | {row['variant']} | "
            f"{_num(row['score'])} / {_num(row['max_score'])} | "
            f"{row['points_full']}/{row['points_partial']}/{row['points_failed']} | "
            f"{_duration(row['duration_sec'])} | {row['status_label']} | "
            f"{f'{delta:+.1f}' if delta is not None else ''} | {_md_cell(note)} |"
        )
    lines.append("")

    if summary["missing"]:
        lines.append("## 未运行 / 未完成")
        lines.append("")
        lines.append("| 套件 | 状态 | 得分 | 满分 | 原因 |")
        lines.append("|---|---|---:|---:|---|")
        for row in summary["missing"]:
            lines.append(
                f"| {row['name']} | {row.get('status_label', row['status'])} | "
                f"{_num(row['score'])} | {_num(row['max_score'])} | {row['reason']} |"
            )
        lines.append("")

    # 分组明细：组内每个测试点（含证据）。
    details = summary.get("group_details", [])
    if details:
        lines.append("## 分组明细")
        lines.append("")
        lines.append(
            "每个分组下面列出没有得到满分的测试点及其证据；全部通过的测试点折叠在末尾。"
        )
        lines.append("")
        for group in details:
            lines.append(
                f"### {group['name']} — {_num(group['score'])} / "
                f"{_num(group['max_score'])}（失 {_num(group['lost'])}）"
            )
            lines.append("")
            lines.append(f"- 套件：{group['title']}（{group['suite']} / {group['variant']}）")
            lines.append(
                f"- 分组状态：{'完整' if group['complete'] else '不完整（缺少 END 标记）'}"
            )
            lines.append(
                f"- 测试点：{group['points_total']} 个（通过 {group['points_full']}，"
                f"部分 {group['points_partial']}，失败 {group['points_failed']}，"
                f"跳过 {group['points_skipped']}）"
            )
            imperfect = _imperfect_points(group)
            silent = _silent_points(group)
            explained = _explained_points(group)
            if imperfect:
                lines.append(
                    f"- 未满分测试点：{len(imperfect)} 个"
                    f"（其中 {len(silent)} 个在串口里没有任何输出）"
                )
            else:
                lines.append("- 全部测试点满分")
            lines.append("")
            if explained:
                lines.append("| 测试点 | 状态 | 得分/满分 | 通过/总数 | 证据 |")
                lines.append("|---|---|---:|---:|---|")
                for point in explained:
                    evidence = _point_evidence_lines(point, limit=4)
                    if point.get("evidence_truncated"):
                        evidence = evidence + ["（没有 END 标记，输出被下一段截断）"]
                    cell = "<br>".join(_md_cell(item) for item in evidence) or "—"
                    lines.append(
                        f"| `{_md_cell(point['name'])}` | {point['status_label']} | "
                        f"{_num(point['score'])} / {_num(point['max_score'])} | "
                        f"{_count_text(point)} | {cell} |"
                    )
                lines.append("")
            if silent:
                lines.append(
                    f"串口里完全没有输出的测试点（judge 直接判 0，共 {len(silent)} 个）："
                    f"{_name_list(silent, limit=40)}"
                )
                lines.append("")
            passed = [
                point
                for point in group.get("points", [])
                if point["status"] == STATUS_FULL and point["lost"] <= 1e-9
            ]
            if passed:
                lines.append(
                    f"<details><summary>其余 {len(passed)} 个测试点（全部通过）</summary>"
                )
                lines.append("")
                lines.append("| 测试点 | 得分/满分 | 通过/总数 | 说明 |")
                lines.append("|---|---:|---:|---|")
                for point in passed:
                    lines.append(
                        f"| `{_md_cell(point['name'])}` | "
                        f"{_num(point['score'])} / {_num(point['max_score'])} | "
                        f"{_count_text(point)} | {_md_cell(_note_text(point))} |"
                    )
                lines.append("")
                lines.append("</details>")
                lines.append("")

    if summary.get("lost_by_suite"):
        lines.append("## 按套件统计失分")
        lines.append("")
        lines.append("| 套件 | 失分 | 测试点数 |")
        lines.append("|---|---:|---:|")
        for row in summary["lost_by_suite"]:
            lines.append(
                f"| {row['name']} | {_num(row['lost'])} | {row['points']} |"
            )
        lines.append("")

    if summary["lost_points"]:
        lines.append("## 失分最多的测试点")
        lines.append("")
        lines.append("| # | 分组 | 测试点 | 状态 | 得分 | 满分 | 失分 | 说明 |")
        lines.append("|---:|---|---|---|---:|---:|---:|---|")
        for index, row in enumerate(summary["lost_points"], 1):
            lines.append(
                f"| {index} | {row['group']} | `{_md_cell(row['name'])}` | "
                f"{row.get('status_label', row['status'])} | "
                f"{_num(row['score'])} | {_num(row['max_score'])} | "
                f"{_num(row['lost'])} | {_md_cell(_note_text(row))} |"
            )
        lines.append("")

    if summary["warnings"]:
        lines.append("## 警告")
        lines.append("")
        for warning in summary["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## 性能指标")
    lines.append("")
    metrics_by_group: dict[str, list[dict[str, Any]]] = {}
    for metric in summary["metrics"]:
        metrics_by_group.setdefault(metric["group"], []).append(metric)
    for group_name in sorted(metrics_by_group, key=_group_order):
        metrics = metrics_by_group[group_name]
        if not metrics:
            continue
        lines.append(f"### {group_name}")
        lines.append("")
        lines.append("| 指标 | 数值 | 单位 | 参考 | 质量 | 变化 |")
        lines.append("|---|---:|---|---:|---:|---:|")
        for metric in metrics:
            ref = "-"
            if metric.get("range_low") is not None:
                ref = (
                    f"{_num(metric['range_low'], 4)}-"
                    f"{_num(metric['range_high'], 4)}"
                )
            elif metric.get("reference") is not None:
                ref = _num(metric["reference"], 4)
            quality = (
                f"{metric['quality']:.3f}"
                if metric.get("quality") is not None
                else ""
            )
            delta = (
                f"{metric['delta_pct']:+.1f}%"
                if metric.get("delta_pct") is not None
                else ""
            )
            lines.append(
                f"| {_md_cell(metric['name'])} | {_num(metric['value'], 4)} | "
                f"{metric['unit']} | {ref} | {quality} | {delta} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 写文件
# ---------------------------------------------------------------------------


def write_reports(report: dict[str, Any], run_dir: Path) -> tuple[Path, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / REPORT_JSON
    md_path = run_dir / REPORT_MD
    txt_path = run_dir / "report.txt"

    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    text = render_text(report)
    txt_path.write_text(text, encoding="utf-8")
    return json_path, md_path, txt_path


def print_report(report: dict[str, Any]) -> None:
    print(render_text(report), end="")
