"""Parse the raw QEMU serial log into test groups and init-script runs.

The parser is intentionally tolerant: the OS may or may not print the
``StarryOS: running ...`` helper lines, and a killed test point may never print
its ``END`` marker.  Scoring decisions are made later; this module only reports
what was seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import GroupResult


# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------

# ANSI colour / cursor sequences.  The guest's busybox occasionally emits them
# (e.g. coloured `ls` output); they must not confuse the judges.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

GROUP_START_RE = re.compile(r"^####\s*OS COMP TEST GROUP START\s+(.+?)\s*####\s*$")
GROUP_END_RE = re.compile(r"^####\s*OS COMP TEST GROUP END\s+(.+?)\s*####\s*$")

SCRIPT_RUN_RE = re.compile(r"^####\s*StarryOS: running\s+(.+?)\s*####\s*$")
SCRIPT_DONE_RE = re.compile(
    r"^####\s*StarryOS: finished\s+(.+?)\s+\(status\s+(-?\d+)\)\s*####\s*$"
)
WATCHDOG_RE = re.compile(r"^####\s*StarryOS: watchdog timeout\s*####\s*$")
FINISHED_RE = re.compile(r"^####\s*StarryOS: all test points finished\s*####\s*$")
ARCH_RE = re.compile(r"^####\s*StarryOS: arch\s+(.+?)\s*####\s*$")

# Known suffix -> libc variant.  ``default`` is used by images whose scripts
# print e.g. ``START basic`` without a libc suffix.
VARIANT_SUFFIXES = ("glibc", "musl")


@dataclass
class RawGroup:
    group: str
    body: str
    complete: bool
    start_line: int
    end_line: int
    duration_sec: float | None = None


@dataclass
class ScriptRun:
    path: str
    script: str
    suite: str
    variant: str
    status: int | None = None
    start_line: int = 0
    end_line: int = 0
    duration_sec: float | None = None


@dataclass
class ParsedLog:
    text: str
    groups: list[RawGroup] = field(default_factory=list)
    script_runs: list[ScriptRun] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    markers: dict[str, str] = field(default_factory=dict)

    def group_bodies(self) -> dict[str, list[RawGroup]]:
        result: dict[str, list[RawGroup]] = {}
        for group in self.groups:
            result.setdefault(group.group, []).append(group)
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clean_serial(text: str) -> str:
    """Normalize a raw serial capture for parsing only.

    The raw file is kept untouched on disk; this function removes carriage
    returns and ANSI escapes and makes sure the text ends with a newline.
    """

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = ANSI_RE.sub("", text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def split_suite_variant(name: str) -> tuple[str, str]:
    """Split ``basic-musl`` into ``("basic", "musl")``.

    Group names that do not carry a known libc suffix keep the whole name as
    the suite and use the ``default`` variant.
    """

    name = name.strip()
    if "-" in name:
        base, suffix = name.rsplit("-", 1)
        if suffix in VARIANT_SUFFIXES:
            return base, suffix
    return name, "default"


def _suite_from_script(path: str) -> str:
    name = Path(path).name
    if name.endswith("_testcode.sh"):
        return name[: -len("_testcode.sh")]
    return ""


def _variant_from_script(path: str) -> str:
    parent = Path(path).parent.name
    if parent in VARIANT_SUFFIXES:
        return parent
    return "default"


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_serial(text: str, timestamps: list[float] | None = None) -> ParsedLog:
    """Parse a serial log.

    ``timestamps`` is optional and, when supplied, must contain one entry per
    line of the *cleaned* text.  It is used to estimate per-group durations
    when the init script does not print ``running``/``finished`` markers.
    """

    cleaned = clean_serial(text)
    lines = cleaned.splitlines()
    parsed = ParsedLog(text=cleaned)

    open_group: RawGroup | None = None
    open_script: ScriptRun | None = None

    for index, line in enumerate(lines):
        stripped = line.strip()

        match = GROUP_START_RE.match(stripped)
        if match:
            if open_group is not None:
                # A test point aborted before its END marker (e.g. an
                # assertion failure).  Keep the partial body for diagnostics.
                open_group.complete = False
                open_group.end_line = index
                parsed.groups.append(open_group)
            open_group = RawGroup(
                group=match.group(1).strip(),
                body="",
                complete=False,
                start_line=index + 1,
                end_line=-1,
            )
            continue

        match = GROUP_END_RE.match(stripped)
        if match:
            name = match.group(1).strip()
            if open_group is not None:
                open_group.complete = True
                open_group.end_line = index + 1
                open_group.body = "\n".join(lines[open_group.start_line : index])
                parsed.groups.append(open_group)
                open_group = None
            else:
                parsed.warnings.append(
                    f"line {index + 1}: END marker for {name!r} without START"
                )
            continue

        match = SCRIPT_RUN_RE.match(stripped)
        if match:
            if open_group is not None:
                # A new script started while the previous group had no END:
                # the previous test point was aborted.
                open_group.complete = False
                open_group.end_line = index
                parsed.groups.append(open_group)
                open_group = None
            if open_script is not None:
                open_script.end_line = index + 1
                parsed.script_runs.append(open_script)
            path = match.group(1).strip()
            open_script = ScriptRun(
                path=path,
                script=Path(path).name,
                suite=_suite_from_script(path),
                variant=_variant_from_script(path),
                start_line=index + 1,
            )
            continue

        match = SCRIPT_DONE_RE.match(stripped)
        if match:
            path = match.group(1).strip()
            status = int(match.group(2))
            if open_script is not None and open_script.path == path:
                open_script.status = status
                open_script.end_line = index + 1
                parsed.script_runs.append(open_script)
                open_script = None
            else:
                run = ScriptRun(
                    path=path,
                    script=Path(path).name,
                    suite=_suite_from_script(path),
                    variant=_variant_from_script(path),
                    status=status,
                    start_line=index + 1,
                    end_line=index + 1,
                )
                parsed.script_runs.append(run)
            continue

        if open_group is not None:
            # Body is collected at the end so that incomplete groups keep their
            # partial output as well.
            continue

        if WATCHDOG_RE.match(stripped):
            parsed.markers["watchdog"] = stripped
        elif FINISHED_RE.match(stripped):
            parsed.markers["finished"] = stripped
        else:
            match = ARCH_RE.match(stripped)
            if match:
                parsed.markers["arch"] = match.group(1).strip()

    if open_group is not None:
        open_group.complete = False
        open_group.end_line = len(lines)
        open_group.body = "\n".join(lines[open_group.start_line :])
        parsed.groups.append(open_group)
        parsed.warnings.append(
            f"group {open_group.group!r} has no END marker (killed or aborted)"
        )

    if open_script is not None:
        open_script.end_line = len(lines)
        parsed.script_runs.append(open_script)
        parsed.warnings.append(
            f"script {open_script.path!r} has no finished marker (killed or aborted)"
        )

    # Fill durations from line timestamps when available.  The init markers
    # give a more accurate value, but a log produced by another harness may
    # not contain them.
    if timestamps and len(timestamps) >= len(lines):
        for run in parsed.script_runs:
            if run.start_line and run.end_line:
                start = timestamps[run.start_line - 1]
                end = timestamps[min(run.end_line - 1, len(timestamps) - 1)]
                if end >= start:
                    run.duration_sec = end - start
        for raw in parsed.groups:
            if raw.start_line and raw.end_line:
                start = timestamps[raw.start_line - 1]
                end = timestamps[min(raw.end_line - 1, len(timestamps) - 1)]
                if end >= start:
                    raw.duration_sec = end - start

    # An incomplete group that was followed by another START is scored as an
    # aborted test point; make sure its body is available.
    for raw in parsed.groups:
        if not raw.body and raw.end_line > raw.start_line:
            raw.body = "\n".join(lines[raw.start_line : raw.end_line])
        if raw.body and not raw.body.endswith("\n"):
            raw.body += "\n"

    return parsed


def raw_group_to_result(raw: RawGroup) -> GroupResult:
    """Create an unscored ``GroupResult`` (scoring fills points/metrics)."""

    suite, variant = split_suite_variant(raw.group)
    return GroupResult(
        group=raw.group,
        suite=suite,
        variant=variant,
        complete=raw.complete,
        body=raw.body,
        start_line=raw.start_line,
        end_line=raw.end_line,
        duration_sec=raw.duration_sec,
    )
