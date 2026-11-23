"""Static configuration: paths, architectures, QEMU command lines.

Everything that depends on the host layout lives here so the rest of the
scorer can be unit-tested with plain strings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Root of the ``starry-eval`` checkout (the directory that contains this
# package).  The scorer is intentionally *outside* the StarryOS repository.
TOOL_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = TOOL_ROOT.parent

# The two architectures required by the competition.
ARCH_ORDER = ("rv", "la")


@dataclass(frozen=True)
class ArchSpec:
    key: str
    label: str
    qemu: str
    kernel_name: str
    image_name: str
    build_target: str


ARCH_SPECS: dict[str, ArchSpec] = {
    "rv": ArchSpec(
        key="rv",
        label="riscv64",
        qemu="qemu-system-riscv64",
        kernel_name="kernel-rv",
        image_name="sdcard-rv.img",
        build_target="rv",
    ),
    "la": ArchSpec(
        key="la",
        label="loongarch64",
        qemu="qemu-system-loongarch64",
        kernel_name="kernel-la",
        image_name="sdcard-la.img",
        build_target="la",
    ),
}


@dataclass
class Paths:
    """Resolved filesystem paths for one invocation.

    ``starry_dir`` is only *one* place a kernel or an image may live.  Two
    layouts must both work without arguments:

    * two repos side by side (``Oops-StarryOS/`` builds the kernel, the images
      sit next to it) -- the competitive layout;
    * this repo alone, with a prebuilt ``kernels/kernel-rv`` and an image in
      ``images/`` -- what a teammate gets when they only want to *score*.

    Hence the per-artifact lookup order in ``kernel`` / ``image``: an explicit
    ``--starry-dir`` entry wins, then the in-repo drop-in directory.
    """

    starry_dir: Path
    judge_dir: Path
    runs_dir: Path
    baseline_file: Path
    tool_root: Path = TOOL_ROOT

    @staticmethod
    def _pick(candidates: list[Path]) -> Path:
        for candidate in candidates:
            if candidate.exists():
                return candidate
        # Nothing exists: return the most specific candidate so the "not found"
        # error names a path the caller can actually create.
        return candidates[0]

    def kernel(self, arch: str) -> Path:
        name = ARCH_SPECS[arch].kernel_name
        return self._pick([self.starry_dir / name, self.tool_root / "kernels" / name])

    def image(self, arch: str) -> Path:
        name = ARCH_SPECS[arch].image_name
        return self._pick([self.starry_dir / name, self.tool_root / "images" / name])


#: Bundled copy of the official ``judge_*.py`` set (see ``vendor/judge``).
VENDOR_JUDGE_DIR = TOOL_ROOT / "vendor" / "judge"

#: The upstream checkout the competition expects next to StarryOS.
SIBLING_JUDGE_DIR = WORKSPACE_ROOT / "oskernel-testsuits-cooperation" / "judge"


#: Directory names accepted for the OS checkout, in preference order.
STARRY_DIR_NAMES = ("StarryOS", "Oops-StarryOS")


def _looks_like_os(path: Path) -> bool:
    """A StarryOS checkout has both a Makefile and a src/ tree."""

    return (path / "Makefile").is_file() and (path / "src").is_dir()


def _default_starry_dir() -> Path:
    """Pick the OS checkout: sibling directory first, then a submodule here."""

    for base in (WORKSPACE_ROOT, TOOL_ROOT):
        for name in STARRY_DIR_NAMES:
            candidate = base / name
            if _looks_like_os(candidate):
                return candidate
    return WORKSPACE_ROOT / "StarryOS"


def _default_judge_dir() -> Path:
    """Pick the judge directory without any user input.

    A full upstream checkout (``../oskernel-testsuits-cooperation/judge``) wins,
    so a developer working on the judge scripts keeps testing their edits.  A
    fresh clone of this tool only has ``vendor/judge`` and must still work.
    When neither exists the sibling path is returned, because that is what the
    error message should point at.
    """

    if SIBLING_JUDGE_DIR.is_dir():
        return SIBLING_JUDGE_DIR
    if VENDOR_JUDGE_DIR.is_dir():
        return VENDOR_JUDGE_DIR
    return SIBLING_JUDGE_DIR


def default_paths(
    starry_dir: str | os.PathLike[str] | None = None,
    judge_dir: str | os.PathLike[str] | None = None,
    runs_dir: str | os.PathLike[str] | None = None,
    baseline_file: str | os.PathLike[str] | None = None,
) -> Paths:
    starry = Path(starry_dir) if starry_dir else _default_starry_dir()
    judge = Path(judge_dir) if judge_dir else _default_judge_dir()
    runs = Path(runs_dir) if runs_dir else TOOL_ROOT / "runs"
    baseline = Path(baseline_file) if baseline_file else TOOL_ROOT / "baseline.json"
    return Paths(
        starry_dir=starry.resolve(),
        judge_dir=judge.resolve(),
        runs_dir=runs.resolve(),
        baseline_file=baseline.resolve(),
        # Read at call time (not as a dataclass default) so tests can patch it.
        tool_root=TOOL_ROOT,
    )


# ---------------------------------------------------------------------------
# QEMU command lines
# ---------------------------------------------------------------------------


def qemu_command(
    arch: str,
    kernel: Path,
    image: Path,
    *,
    memory: str = "1G",
    smp: int = 1,
    snapshot: bool = True,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the same QEMU invocation that the competition uses.

    The command is copied from the official evaluation guide (and from the
    ``run-rv`` / ``run-la`` targets of the StarryOS Makefile).  ``-snapshot``
    is added by default so repeated local runs always start from the pristine
    disk image; pass ``snapshot=False`` when a test really needs persistence.
    """

    spec = ARCH_SPECS[arch]
    if arch == "rv":
        command = [
            spec.qemu,
            "-machine",
            "virt",
            "-kernel",
            str(kernel),
            "-m",
            memory,
            "-nographic",
            "-smp",
            str(smp),
            "-bios",
            "default",
            "-drive",
            f"file={image},if=none,format=raw,id=x0",
            "-device",
            "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
            "-no-reboot",
            "-device",
            "virtio-net-device,netdev=net",
            "-netdev",
            "user,id=net",
            "-rtc",
            "base=utc",
        ]
    elif arch == "la":
        command = [
            spec.qemu,
            "-kernel",
            str(kernel),
            "-m",
            memory,
            "-nographic",
            "-smp",
            str(smp),
            "-drive",
            f"file={image},if=none,format=raw,id=x0",
            "-device",
            "virtio-blk-pci,drive=x0",
            "-no-reboot",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-netdev",
            "user,id=net0,hostfwd=tcp::5555-:5555,hostfwd=udp::5555-:5555",
            "-rtc",
            "base=utc",
        ]
    else:
        raise ValueError(f"unknown architecture: {arch!r}")

    if snapshot:
        command.append("-snapshot")
    if extra_args:
        command.extend(extra_args)
    return command


# ---------------------------------------------------------------------------
# Timeouts / limits
# ---------------------------------------------------------------------------

# The guest init installs a 1800 s watchdog; leave enough room for a clean
# shutdown and for a slow TCG boot.
DEFAULT_QEMU_TIMEOUT = 2400.0

# ``init.sh`` currently allows 240 s for fast/medium scripts and 600 s for
# unixbench.  Used only to warn when a group is close to being killed.
DEFAULT_SCRIPT_TIMEOUT = 240.0
DEFAULT_LONG_SCRIPT_TIMEOUT = 600.0

# Default number of filesystem blocks kept in the serial log.  The full log is
# always written to disk; this only limits the in-memory copy.
MAX_SERIAL_LINES = 400_000


# Directories that contain the official judge scripts.  These are searched in
# order when --judge-dir is not supplied.
JUDGE_DIR_CANDIDATES = (
    WORKSPACE_ROOT / "oskernel-testsuits-cooperation" / "judge",
    TOOL_ROOT / "vendor" / "judge",
)
