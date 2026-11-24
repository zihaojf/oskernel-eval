"""Command-line interface for the local StarryOS scorer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compare import compare_files
from .config import (
    ARCH_ORDER,
    ARCH_SPECS,
    DEFAULT_QEMU_TIMEOUT,
    TOOL_ROOT,
    default_paths,
)
from .history import REPORT_JSON, RunStore
from .online import (
    DEFAULT_ONLINE_REFERENCE,
    build_comparison,
    load_online_reference,
    load_report,
    render_markdown as render_online_markdown,
    render_text as render_online_text,
)
from .qemu import RunnerError
from .references import OFFICIAL_SUITES, SUITE_ORDER, SUITE_SPECS
from .report import render_markdown, render_text
from .runner import BuildError, build_kernel, run_one, score_one


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--starry-dir", help="StarryOS checkout (default: ../StarryOS)")
    parser.add_argument(
        "--judge-dir",
        help="directory containing judge_*.py "
        "(default: ../oskernel-testsuits-cooperation/judge)",
    )
    parser.add_argument(
        "--runs-dir", help="where to store serial logs and reports (default: runs/)"
    )
    parser.add_argument(
        "--baseline",
        help="performance baseline JSON (default: baseline.json next to this tool)",
    )
    parser.add_argument(
        "--score-mode",
        choices=("both", "raw", "intended"),
        default="both",
        help="basic judge compatibility mode (default: both)",
    )
    parser.add_argument(
        "--no-previous-baseline",
        action="store_true",
        help="do not use the previous run as a performance baseline",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="write the metrics of this run to the baseline file",
    )
    parser.add_argument(
        "--format",
        choices=("text", "md", "json", "none"),
        default="text",
        help="what to print (reports are always written to the run directory)",
    )
    parser.add_argument(
        "--allow-missing-judges",
        action="store_true",
        help="continue when the official judge directory is missing",
    )


def _paths_from_args(args: argparse.Namespace):
    judge_dir = Path(args.judge_dir) if args.judge_dir else None
    if judge_dir is not None and not judge_dir.is_dir():
        raise RunnerError(f"找不到 judge 目录：{judge_dir}")
    paths = default_paths(
        starry_dir=args.starry_dir,
        judge_dir=judge_dir,
        runs_dir=args.runs_dir,
        baseline_file=args.baseline,
    )
    if not paths.judge_dir.is_dir() and not args.allow_missing_judges:
        raise RunnerError(
            f"找不到官方 judge 目录：{paths.judge_dir}\n"
            "请用 --judge-dir 指定，或加 --allow-missing-judges 在无 judge 的情况下打分"
        )
    if not paths.judge_dir.is_dir():
        print(
            f"警告：找不到 judge 目录（{paths.judge_dir}），官方套件不会被打分",
            file=sys.stderr,
        )
    return paths


def _print_report(report: dict, fmt: str) -> None:
    if fmt == "text":
        print(render_text(report), end="")
    elif fmt == "md":
        print(render_markdown(report), end="")
    elif fmt == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif fmt == "none":
        print(f"报告：{Path(report['run_dir']) / REPORT_JSON}")


def _strict_ok(report: dict) -> bool:
    summary = report.get("summary", {})
    counts = summary.get("counts", {})
    if report.get("timed_out"):
        return False
    return counts.get("groups", 0) > 0 and counts.get("groups_incomplete", 0) == 0


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    store = RunStore(paths.runs_dir)
    archs = list(ARCH_ORDER) if args.arch == "all" else [args.arch]

    reports: list[tuple[str, dict, Path]] = []
    for arch in archs:
        report, run_dir = run_one(
            arch,
            paths,
            store,
            timeout=args.timeout,
            snapshot=not args.no_snapshot,
            build=args.build,
            score_mode=args.score_mode,
            baseline_file=Path(args.baseline) if args.baseline else None,
            use_previous_baseline=not args.no_previous_baseline,
            save_baseline_after=args.save_baseline,
        )
        reports.append((arch, report, run_dir))
        _print_report(report, args.format)
        print()

    if len(reports) > 1:
        print("=" * 78)
        print("两架构合计")
        print("=" * 78)
        for arch, report, run_dir in reports:
            summary = report["summary"]
            official = summary["official"]
            print(
                f"{arch:<3} 官方 {official['score']:>9.2f}/{official['max_score']:<9.2f}"
                f"  综合 {summary['composite']['score']:>9.2f}/"
                f"{summary['composite']['max_score']:<9.2f}  {run_dir.name}"
            )
        print()

    if args.strict:
        return 0 if all(_strict_ok(report) for _, report, _ in reports) else 1
    return 0


def _infer_arch(text: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    lowered = text.lower()
    if "loongarch64" in lowered or "loongarch" in lowered:
        return "la"
    return "rv"


def cmd_build(args: argparse.Namespace) -> int:
    """Build kernel-rv / kernel-la from the OS checkout, without booting."""

    paths = default_paths(
        starry_dir=args.starry_dir,
        runs_dir=args.runs_dir,
        baseline_file=args.baseline,
    )
    archs = list(ARCH_ORDER) if args.arch == "all" else [args.arch]
    print(f"==> OS 目录  ：{paths.starry_dir}")
    for arch in archs:
        build_kernel(arch, paths.starry_dir)
    for arch in archs:
        kernel = paths.kernel(arch)
        state = "就绪" if kernel.is_file() else "缺失"
        print(f"==> {arch:<3} 内核：{kernel}  （{state}）")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    store = RunStore(paths.runs_dir)
    reports: list[tuple[str, dict, Path]] = []

    for log_name in args.log:
        log_path = Path(log_name)
        if not log_path.is_file():
            raise RunnerError(f"找不到日志文件：{log_path}")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        arch = _infer_arch(text, args.arch)
        label = args.label or f"来自 {log_path.name} 的 {ARCH_SPECS[arch].label}"
        report, run_dir = score_one(
            arch,
            label,
            log_path,
            paths,
            store,
            score_mode=args.score_mode,
            baseline_file=Path(args.baseline) if args.baseline else None,
            use_previous_baseline=not args.no_previous_baseline,
            save_baseline_after=args.save_baseline,
        )
        reports.append((arch, report, run_dir))
        _print_report(report, args.format)
        print()

    if args.strict:
        return 0 if all(_strict_ok(report) for _, report, _ in reports) else 1
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    print(compare_files(Path(args.old), Path(args.new)), end="")
    return 0


def _latest_complete_report(store: RunStore, arch: str) -> Path | None:
    entries = [
        entry
        for entry in store.load_index()
        if entry.get("arch") == arch
        and not entry.get("timed_out")
        and entry.get("groups", 0) > 0
        and entry.get("groups_complete", 0) == entry.get("groups", 0)
    ]
    for entry in reversed(entries):
        path = Path(entry.get("run_dir", "")) / REPORT_JSON
        if path.is_file():
            return path
    return None


def cmd_compare_online(args: argparse.Namespace) -> int:
    paths = default_paths(runs_dir=args.runs_dir)
    store = RunStore(paths.runs_dir)
    online_path = Path(args.online) if args.online else DEFAULT_ONLINE_REFERENCE
    if not online_path.is_file():
        raise RunnerError(f"找不到线上参考文件：{online_path}")
    online_ref = load_online_reference(online_path)

    report_paths: dict[str, Path] = {}
    for arch, explicit in (("rv", args.rv), ("la", args.la)):
        if explicit:
            path = Path(explicit)
        else:
            path = _latest_complete_report(store, arch)
        if path is None or not path.is_file():
            raise RunnerError(
                f"没有 {arch} 的完整本地报告：请用 --{arch} <report.json> 指定，"
                "或先跑一次评分"
            )
        report_paths[arch] = path

    reports = {arch: load_report(path) for arch, path in report_paths.items()}
    comparison = build_comparison(reports, online_ref)
    comparison["local_reports"] = {
        arch: str(path) for arch, path in report_paths.items()
    }

    if args.format == "md":
        print(render_online_markdown(comparison), end="")
    elif args.format == "json":
        print(json.dumps(comparison, indent=2, ensure_ascii=False))
    else:
        print(render_online_text(comparison), end="")
    return 0


def cmd_suites(args: argparse.Namespace) -> int:
    paths = default_paths(
        starry_dir=args.starry_dir,
        judge_dir=args.judge_dir,
        runs_dir=args.runs_dir,
        baseline_file=args.baseline,
    )
    print(
        f"{'套件':<12} {'方式':<10} {'类型':<12} {'满分':>6}  {'官方 judge':<14} 说明"
    )
    print("-" * 90)
    for name in SUITE_ORDER:
        spec = SUITE_SPECS[name]
        judge = (
            "有"
            if name in OFFICIAL_SUITES and (paths.judge_dir / f"judge_{name}.py").is_file()
            else "无"
        )
        max_text = "-" if spec.default_max is None else str(int(spec.default_max))
        print(
            f"{name:<12} {spec.mode:<10} {spec.kind:<12} {max_text:>6}  "
            f"{judge:<14} {spec.title}"
        )
    print()
    print(f"judge 目录：{paths.judge_dir}")
    print(f"OS 目录   ：{paths.starry_dir}")
    print(f"运行目录  ：{paths.runs_dir}")
    print(f"基线文件  ：{paths.baseline_file}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from .history import load_baseline

    paths = default_paths(
        starry_dir=args.starry_dir,
        judge_dir=args.judge_dir,
        runs_dir=args.runs_dir,
        baseline_file=args.baseline,
    )
    data = load_baseline(paths.baseline_file)
    if not data:
        print(f"没有基线文件：{paths.baseline_file}")
        return 0
    print(f"基线：{paths.baseline_file}（{len(data)} 项指标）")
    for key in sorted(data):
        print(f"  {key:<72} {data[key]:g}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="starry-eval",
        description=(
            "Local scorer for the OS competition disk images.  It boots the "
            "StarryOS kernel in QEMU, captures the serial log, runs the "
            "official judges and writes a report.  It never modifies StarryOS."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="boot the kernel(s) and score the live output")
    _add_common(run)
    run.add_argument("--arch", choices=(*ARCH_ORDER, "all"), default="all")
    run.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_QEMU_TIMEOUT,
        help=f"QEMU wall-clock timeout in seconds (default: {DEFAULT_QEMU_TIMEOUT:g})",
    )
    run.add_argument("--no-snapshot", action="store_true", help="write to the disk image")
    run.add_argument("--build", action="store_true", help="run make <arch> first")
    run.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on timeout, no groups, or incomplete groups",
    )
    run.set_defaults(func=cmd_run)

    build = sub.add_parser(
        "build",
        help="build kernel-rv / kernel-la from the OS checkout (no QEMU boot)",
    )
    _add_common(build)
    build.add_argument("--arch", choices=(*ARCH_ORDER, "all"), default="all")
    build.set_defaults(func=cmd_build)

    score = sub.add_parser("score", help="score one or more saved serial logs")
    _add_common(score)
    score.add_argument("log", nargs="+", help="serial log file(s)")
    score.add_argument("--arch", choices=ARCH_ORDER, help="override architecture detection")
    score.add_argument("--label", help="label stored in the report")
    score.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on no groups or incomplete groups",
    )
    score.set_defaults(func=cmd_score)

    compare = sub.add_parser("compare", help="compare two report.json files")
    compare.add_argument("old", help="older report.json")
    compare.add_argument("new", help="newer report.json")
    compare.set_defaults(func=cmd_compare)

    compare_online = sub.add_parser(
        "compare-online",
        help="align local reports with a saved online evaluation result",
    )
    compare_online.add_argument(
        "--online",
        help=f"online reference JSON (default: {DEFAULT_ONLINE_REFERENCE.name})",
    )
    compare_online.add_argument("--rv", help="local RISC-V report.json")
    compare_online.add_argument("--la", help="local LoongArch report.json")
    compare_online.add_argument("--runs-dir", help="where to auto-detect reports")
    compare_online.add_argument(
        "--format", choices=("text", "md", "json"), default="text"
    )
    compare_online.set_defaults(func=cmd_compare_online)

    suites = sub.add_parser("suites", help="list the suite catalogue")
    _add_common(suites)
    suites.set_defaults(func=cmd_suites)

    baseline = sub.add_parser("baseline", help="show the saved performance baseline")
    _add_common(baseline)
    baseline.set_defaults(func=cmd_baseline)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (RunnerError, BuildError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
