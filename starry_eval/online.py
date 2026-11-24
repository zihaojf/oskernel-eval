"""Compare local reports with a saved online evaluation result.

The online table uses a different suite set and different per-suite formulae
(notably libcbench / netperf / cyclictest), so a raw "local composite vs online
total" comparison is meaningless.  This module aligns both sides on the online
environment/suite matrix and shows exactly where the remaining differences are.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import TOOL_ROOT
from .references import SUITE_ORDER


DEFAULT_ONLINE_REFERENCE = TOOL_ROOT / "online_2026.json"

ONLINE_SUITE_ORDER = (
    "basic",
    "busybox",
    "cyclictest",
    "iozone",
    "iperf",
    "libcbench",
    "libctest",
    "lmbench",
    "ltp",
    "lua",
    "netperf",
)


def load_online_reference(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "environments" not in data:
        raise ValueError(f"not an online reference file: {path}")
    return data


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _suite_map(report: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in report.get("summary", {}).get("suite_rows", []):
        key = (row.get("suite", ""), row.get("variant", ""))
        result[key] = row
    return result


def _local_value(
    suite_map: dict[tuple[str, str], dict[str, Any]],
    suite: str,
    variant: str,
) -> tuple[float | None, str]:
    row = suite_map.get((suite, variant))
    if row is None:
        return None, "missing"
    if not row.get("ran", False):
        return None, "not-run"
    return float(row.get("score", 0.0)), str(row.get("status", "ok"))


def build_comparison(
    reports: dict[str, dict[str, Any]],
    online: dict[str, Any],
) -> dict[str, Any]:
    """Build a per-environment / per-suite comparison structure."""

    environments: dict[str, Any] = {}
    totals = {"local": 0.0, "online": 0.0, "local_extra": 0.0}

    for env, online_suites in online.get("environments", {}).items():
        # glibc-la -> variant=glibc, arch=la
        if "-" not in env:
            continue
        variant, arch = env.rsplit("-", 1)
        report = reports.get(arch)
        if report is None:
            environments[env] = {
                "error": f"没有 {arch} 架构的本地报告",
                "local_total": None,
                "online_total": None,
                "rows": {},
            }
            continue

        suite_map = _suite_map(report)
        rows: dict[str, Any] = {}
        local_total = 0.0
        online_total = 0.0

        for suite in ONLINE_SUITE_ORDER:
            if suite not in online_suites:
                continue
            online_value = online_suites[suite]
            local_value, local_status = _local_value(suite_map, suite, variant)
            row: dict[str, Any] = {
                "suite": suite,
                "local": local_value,
                "online": online_value,
                "local_status": local_status,
                "delta": None,
                "counted": online_value is not None,
            }
            if online_value is not None:
                online_total += float(online_value)
                if local_value is not None:
                    local_total += float(local_value)
                    row["delta"] = float(local_value) - float(online_value)
                else:
                    row["delta"] = 0.0 - float(online_value)
            rows[suite] = row

        # Local suites that are not part of the online matrix.
        extra: dict[str, float] = {}
        for (suite, suite_variant), value in suite_map.items():
            if suite_variant != variant:
                continue
            if suite in online_suites:
                continue
            if not value.get("ran", False):
                continue
            score = float(value.get("score", 0.0))
            if score > 0:
                extra[suite] = score

        environments[env] = {
            "arch": arch,
            "variant": variant,
            "local_total": local_total,
            "online_total": online_total,
            "delta": local_total - online_total,
            "rows": rows,
            "local_extra": extra,
        }
        totals["local"] += local_total
        totals["online"] += online_total
        totals["local_extra"] += sum(extra.values())

    totals["delta"] = totals["local"] - totals["online"]
    return {
        "source": online.get("source", ""),
        "online_total": float(online.get("total", totals["online"])),
        "environments": environments,
        "totals": totals,
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _fmt_signed(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:+.{digits}f}"


def _fmt_cell(row: dict[str, Any]) -> str:
    local = _fmt(row.get("local"))
    online = _fmt(row.get("online"))
    if row.get("online") is None:
        return f"{local}/-"
    delta = row.get("delta")
    return f"{local}/{online}/{_fmt(delta, 2)}"


def render_text(comparison: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 92)
    lines.append("线上 / 本地对比")
    lines.append("=" * 92)
    lines.append(f"线上数据来源：{comparison.get('source') or '-'}")
    for arch, path in comparison.get("local_reports", {}).items():
        lines.append(f"本地 {arch}：{path}")
    lines.append(
        "单元格：本地/线上/差值（'-' = 线上不计分，'n.a.' = 本地没跑）"
    )
    lines.append("")

    lines.append("各环境总分（对齐线上的套件集合）")
    lines.append("-" * 92)
    lines.append(f"{'环境':<14} {'本地':>10} {'线上':>10} {'差值':>10}")
    for env, data in comparison["environments"].items():
        if data.get("error"):
            lines.append(f"{env:<14} {data['error']}")
            continue
        lines.append(
            f"{env:<14} {_fmt(data['local_total']):>10} "
            f"{_fmt(data['online_total']):>10} "
            f"{_fmt_signed(data['delta']):>10}"
        )
    totals = comparison["totals"]
    lines.append("-" * 92)
    lines.append(
        f"{'合计':<14} {_fmt(totals['local']):>10} "
        f"{_fmt(totals['online']):>10} {_fmt_signed(totals['delta']):>10}"
    )
    lines.append("")

    env_names = list(comparison["environments"].keys())
    lines.append("各套件对比")
    lines.append("-" * 92)
    header = f"{'套件':<12}" + "".join(f"{env:<20}" for env in env_names)
    lines.append(header)
    for suite in ONLINE_SUITE_ORDER:
        cells: list[str] = []
        any_row = False
        for env in env_names:
            data = comparison["environments"][env]
            row = data.get("rows", {}).get(suite)
            if row is None:
                cells.append("-".ljust(20))
            else:
                any_row = True
                cells.append(_fmt_cell(row).ljust(20))
        if any_row:
            lines.append(f"{suite:<12}" + "".join(cells))
    lines.append("")

    extras: list[str] = []
    for env, data in comparison["environments"].items():
        for suite, score in data.get("local_extra", {}).items():
            extras.append(f"{env}: {suite} = {_fmt(score)}")
    if extras:
        lines.append("只有本地有的套件（不在线上表里，未计入上面的合计）")
        lines.append("-" * 92)
        for item in extras:
            lines.append(f"  {item}")
        lines.append(
            f"  本地独有合计：{_fmt(comparison['totals']['local_extra'])}"
        )
        lines.append("")

    lines.append("原始总分为什对不上")
    lines.append("-" * 92)
    lines.append("  1. unixbench 计入了本地综合分，线上没有这个套件。")
    lines.append("  2. libctest-glibc 线上是 '-'（不计分/没抓到），本地会跑")
    lines.append("     这一项（172/174）；两架构合计就差了约 346 分。")
    lines.append("  3. libcbench/netperf/cyclictest 的计分公式不同：")
    lines.append("     本地 = 覆盖率/成功数；线上 = 性能/有效性打分。")
    lines.append("  4. iozone 只是每次运行的吞吐/基线不同。")
    lines.append("  5. 线上总分 = 4 个环境（glibc/musl × la/rv）；本地报告是")
    lines.append("     按架构分开的，请对比按环境对齐的那些行。")
    return "\n".join(lines) + "\n"


def render_markdown(comparison: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 线上 / 本地对比")
    lines.append("")
    lines.append(f"- 线上数据来源：{comparison.get('source') or '-'}")
    for arch, path in comparison.get("local_reports", {}).items():
        lines.append(f"- 本地 {arch}：{path}")
    lines.append("- 单元格：`本地/线上/差值`；`-` = 线上不计分")
    lines.append("")
    lines.append("## 每个环境的总分（对齐在线套件集合）")
    lines.append("")
    lines.append("| 环境 | 本地 | 线上 | 差值 |")
    lines.append("|---|---:|---:|---:|")
    for env, data in comparison["environments"].items():
        if data.get("error"):
            lines.append(f"| {env} | {data['error']} | - | - |")
            continue
        lines.append(
            f"| {env} | {_fmt(data['local_total'])} | "
            f"{_fmt(data['online_total'])} | {_fmt(data['delta'])} |"
        )
    totals = comparison["totals"]
    lines.append(
        f"| **合计** | **{_fmt(totals['local'])}** | "
        f"**{_fmt(totals['online'])}** | **{_fmt(totals['delta'])}** |"
    )
    lines.append("")

    env_names = list(comparison["environments"].keys())
    lines.append("## 分项对比")
    lines.append("")
    lines.append("| 套件 | " + " | ".join(env_names) + " |")
    lines.append("|---|" + "---|" * len(env_names))
    for suite in ONLINE_SUITE_ORDER:
        cells: list[str] = []
        any_row = False
        for env in env_names:
            row = comparison["environments"][env].get("rows", {}).get(suite)
            if row is None:
                cells.append("-")
            else:
                any_row = True
                cells.append(_fmt_cell(row))
        if any_row:
            lines.append(f"| {suite} | " + " | ".join(cells) + " |")
    lines.append("")

    extras: list[str] = []
    for env, data in comparison["environments"].items():
        for suite, score in data.get("local_extra", {}).items():
            extras.append(f"{env}: {suite} = {_fmt(score)}")
    if extras:
        lines.append("## 本地有、线上没有的套件")
        lines.append("")
        for item in extras:
            lines.append(f"- {item}")
        lines.append(
            f"- 合计：{_fmt(comparison['totals']['local_extra'])}（未计入上面的 local total）"
        )
        lines.append("")
    return "\n".join(lines) + "\n"
