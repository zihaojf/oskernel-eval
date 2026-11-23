"""High-level orchestration: build, boot, parse, score, persist."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ARCH_SPECS, DEFAULT_QEMU_TIMEOUT, Paths
from .evaluate import evaluate_text
from .history import (
    REPORT_JSON,
    RunStore,
    load_baseline,
    metrics_from_report,
    metrics_from_results,
    save_baseline,
)
from .qemu import RunnerError, run_qemu
from .report import build_report, write_reports


class BuildError(RuntimeError):
    pass


def build_kernel(arch: str, starry_dir: Path) -> None:
    spec = ARCH_SPECS[arch]
    print(f"==> building {spec.kernel_name} (make {spec.build_target})")
    proc = subprocess.run(
        ["make", "-C", str(starry_dir), spec.build_target],
        text=True,
        capture_output=False,
        check=False,
    )
    if proc.returncode != 0:
        raise BuildError(
            f"make {spec.build_target} failed with exit code {proc.returncode}"
        )


def _load_previous_report(
    store: RunStore, arch: str, run_dir: Path
) -> tuple[dict[str, Any] | None, Path | None]:
    entry = store.previous(arch, run_dir)
    if not entry:
        return None, None
    path = Path(entry["run_dir"]) / REPORT_JSON
    if not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except (OSError, json.JSONDecodeError):
        return None, None


def _baseline_for(
    paths: Paths,
    store: RunStore,
    arch: str,
    run_dir: Path,
    *,
    baseline_file: Path | None,
    use_previous: bool,
) -> tuple[dict[str, float], str]:
    if baseline_file is not None:
        data = load_baseline(baseline_file)
        return data, str(baseline_file) if data else "empty baseline file"

    data = load_baseline(paths.baseline_file)
    if data:
        return data, str(paths.baseline_file)

    if use_previous:
        previous_report, path = _load_previous_report(store, arch, run_dir)
        if previous_report is not None and path is not None:
            return metrics_from_report(previous_report), f"previous run: {path.parent.name}"

    return {}, "none"


def run_one(
    arch: str,
    paths: Paths,
    store: RunStore,
    *,
    timeout: float = DEFAULT_QEMU_TIMEOUT,
    snapshot: bool = True,
    build: bool = False,
    score_mode: str = "both",
    baseline_file: Path | None = None,
    use_previous_baseline: bool = True,
    save_baseline_after: bool = False,
) -> tuple[dict[str, Any], Path]:
    spec = ARCH_SPECS[arch]
    if build:
        build_kernel(arch, paths.starry_dir)

    kernel = paths.kernel(arch)
    image = paths.image(arch)
    if not kernel.is_file():
        raise RunnerError(
            f"kernel not found: {kernel}\n"
            f"build it with 'make {spec.build_target}' or pass --build"
        )
    if not image.is_file():
        raise RunnerError(f"disk image not found: {image}")

    run_dir = store.new_run_dir(arch)
    log_path = run_dir / "serial.log"
    previous_report, _ = _load_previous_report(store, arch, run_dir)
    baseline, baseline_source = _baseline_for(
        paths,
        store,
        arch,
        run_dir,
        baseline_file=baseline_file,
        use_previous=use_previous_baseline,
    )

    print(f"==> booting {arch} ({spec.label})")
    output = run_qemu(
        arch,
        kernel,
        image,
        log_path,
        timeout=timeout,
        snapshot=snapshot,
    )

    result = evaluate_text(
        arch,
        spec.label,
        output.text,
        judge_dir=paths.judge_dir,
        baseline=baseline,
        score_mode=score_mode,
        kernel=str(kernel),
        image=str(image),
        command=output.command,
        started_at=output.started_at,
        wall_time_sec=output.wall_time_sec,
        timed_out=output.timed_out,
        exit_code=output.exit_code,
        log_path=str(log_path),
        script_times=output.script_times,
    )
    result.warnings.append(f"baseline source: {baseline_source}")

    report = build_report(result, run_dir, previous=previous_report)
    write_reports(report, run_dir)
    store.append_index(report["index_entry"])

    if save_baseline_after:
        metrics = metrics_from_results([result])
        existing = load_baseline(paths.baseline_file)
        existing.update(metrics)
        save_baseline(paths.baseline_file, existing)
        print(f"==> baseline updated: {paths.baseline_file} ({len(metrics)} metrics)")

    return report, run_dir


def score_one(
    arch: str,
    label: str,
    log_path: Path,
    paths: Paths,
    store: RunStore,
    *,
    score_mode: str = "both",
    baseline_file: Path | None = None,
    use_previous_baseline: bool = True,
    save_baseline_after: bool = False,
) -> tuple[dict[str, Any], Path]:
    if not log_path.is_file():
        raise RunnerError(f"log file not found: {log_path}")

    run_dir = store.new_run_dir(arch, tag="score")
    serial_path = store.copy_log(log_path, run_dir)
    text = serial_path.read_text(encoding="utf-8", errors="replace")
    started_at = datetime.fromtimestamp(log_path.stat().st_mtime).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    previous_report, _ = _load_previous_report(store, arch, run_dir)
    baseline, baseline_source = _baseline_for(
        paths,
        store,
        arch,
        run_dir,
        baseline_file=baseline_file,
        use_previous=use_previous_baseline,
    )

    result = evaluate_text(
        arch,
        label,
        text,
        judge_dir=paths.judge_dir,
        baseline=baseline,
        score_mode=score_mode,
        kernel=str(paths.kernel(arch)),
        image=str(paths.image(arch)),
        started_at=started_at,
        wall_time_sec=0.0,
        timed_out=False,
        exit_code=None,
        log_path=str(serial_path),
    )
    result.warnings.append(f"baseline source: {baseline_source}")

    report = build_report(result, run_dir, previous=previous_report)
    write_reports(report, run_dir)
    store.append_index(report["index_entry"])

    if save_baseline_after:
        metrics = metrics_from_results([result])
        existing = load_baseline(paths.baseline_file)
        existing.update(metrics)
        save_baseline(paths.baseline_file, existing)
        print(f"==> baseline updated: {paths.baseline_file} ({len(metrics)} metrics)")

    return report, run_dir
