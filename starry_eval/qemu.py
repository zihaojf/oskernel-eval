"""Run one kernel in QEMU and capture its serial console.

The reader records timestamps for the ``StarryOS: running/finished`` helper
lines while the output is streamed.  This gives per-test-point durations
without adding timestamps to the serial log itself (which would break the
verbatim judges).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import ARCH_SPECS, DEFAULT_QEMU_TIMEOUT, MAX_SERIAL_LINES, qemu_command


class RunnerError(RuntimeError):
    pass


@dataclass
class RunOutput:
    text: str
    log_path: Path
    command: list[str] = field(default_factory=list)
    wall_time_sec: float = 0.0
    timed_out: bool = False
    exit_code: int | None = None
    script_times: dict[str, tuple[float, float]] = field(default_factory=dict)
    started_at: str = ""

    @property
    def truncated(self) -> bool:
        return self.text.endswith("\n[starry-eval: in-memory log truncated]\n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def _kill_process_group(proc: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


def check_environment(arch: str, kernel: Path, image: Path) -> None:
    spec = ARCH_SPECS[arch]
    if shutil.which(spec.qemu) is None:
        raise RunnerError(
            f"{spec.qemu} not found in PATH; install QEMU or run inside the "
            "competition Docker image"
        )
    if not kernel.is_file():
        raise RunnerError(
            f"kernel not found: {kernel}\n"
            f"build it first (make {spec.build_target}) or pass --starry-dir"
        )
    if not image.is_file():
        raise RunnerError(f"disk image not found: {image}")


def run_qemu(
    arch: str,
    kernel: Path,
    image: Path,
    log_path: Path,
    *,
    timeout: float = DEFAULT_QEMU_TIMEOUT,
    snapshot: bool = True,
    memory: str = "1G",
    smp: int = 1,
    extra_args: list[str] | None = None,
    verbose: bool = False,
) -> RunOutput:
    """Boot ``kernel`` with ``image`` and return everything that was printed."""

    check_environment(arch, kernel, image)
    command = qemu_command(
        arch,
        kernel,
        image,
        memory=memory,
        smp=smp,
        snapshot=snapshot,
        extra_args=extra_args,
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    line_count = 0
    truncated = False
    script_times: dict[str, tuple[float, float]] = {}
    open_scripts: dict[str, float] = {}
    started_at = _utc_now()
    start = time.monotonic()
    timed_out = False

    # Imported lazily to keep this module independent of the parser's regexes.
    from .serial_parser import SCRIPT_DONE_RE, SCRIPT_RUN_RE

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        if verbose:
            print("+ " + " ".join(command))
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )

        def reader() -> None:
            nonlocal line_count, truncated
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                now = time.monotonic()
                line_count += 1
                if not truncated:
                    if line_count <= MAX_SERIAL_LINES:
                        lines.append(raw_line)
                    else:
                        truncated = True
                        lines.append("\n[starry-eval: in-memory log truncated]\n")
                try:
                    log_file.write(raw_line)
                    log_file.flush()
                except (BrokenPipeError, ValueError):
                    pass

                stripped = raw_line.strip()
                run = SCRIPT_RUN_RE.match(stripped)
                if run:
                    open_scripts[run.group(1).strip()] = now
                    continue
                done = SCRIPT_DONE_RE.match(stripped)
                if done:
                    path = done.group(1).strip()
                    begin = open_scripts.pop(path, None)
                    if begin is not None:
                        script_times[path] = (begin, now)

        thread = threading.Thread(target=reader, name="qemu-serial-reader", daemon=True)
        thread.start()

        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc, signal.SIGTERM)
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill_process_group(proc, signal.SIGKILL)
                exit_code = proc.wait(timeout=10)

        thread.join(timeout=10)
        if thread.is_alive():
            # The pipe may still be open in a grandchild; do not hang the
            # scorer forever.
            try:
                proc.stdout.close()  # type: ignore[union-attr]
            except Exception:
                pass
            thread.join(timeout=2)

    wall_time = time.monotonic() - start
    text = "".join(lines)
    if truncated:
        text += "\n[starry-eval: in-memory log truncated]\n"

    return RunOutput(
        text=text,
        log_path=log_path,
        command=command,
        wall_time_sec=wall_time,
        timed_out=timed_out,
        exit_code=exit_code,
        script_times=script_times,
        started_at=started_at,
    )
