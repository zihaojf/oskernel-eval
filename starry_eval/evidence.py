"""把分组原始输出切成「一个测试点一段」，供报告解释失分原因。

报告里的一个测试点是分组表里的一行（例如 ``test_mount``）。分组的 ``body``
是原样保留的串口输出，本模块把它切成按测试点命名的段落，这样报告就能回答
「这个测试点为什么没拿到满分」，而不只是一个数字。

四种段落标记都要支持，因为不同套件由不同的脚本打印：

* ``========== START test_mount ==========`` … ``========== END test_mount ==========``
  （basic、libctest）
* ``====== iperf BASIC_TCP begin ======`` … ``====== iperf BASIC_TCP end: success ======``
  （iperf、netperf、cyclictest）
* ``testcase lua sort.lua success``（lua，一行一个结果）
* ``RUN LTP CASE fs_atime`` … ``FAIL LTP CASE fs_atime : 1``（ltp）

段落拿不到时退回「正文里包含测试点名字的行」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 报告里每个测试点最多显示几行证据。
MAX_EVIDENCE_LINES = 4
# 单行证据的最大长度（串口输出常常夹杂整表数据）。
MAX_LINE_CHARS = 160

_BLOCK_START = re.compile(r"^=+\s*START\s+(?P<name>.+?)\s*=+$")
_BLOCK_END = re.compile(r"^=+\s*END\s+(?P<name>.+?)\s*=+$")

_CASE_START = re.compile(r"^=+\s*\w+\s+(?P<name>[^=\s]+)\s+begin\s*=+$")
_CASE_END = re.compile(r"^=+\s*\w+\s+(?P<name>[^=\s]+)\s+end:.*=+$")

_LUA_CASE = re.compile(r"^testcase\s+(?P<name>.+?)\s+(?P<status>success|fail)\s*$")

_LTP_START = re.compile(r"^RUN LTP CASE\s+(?P<name>.+?)\s*$")
_LTP_END = re.compile(r"^FAIL LTP CASE\s+(?P<name>.+?)\s*:\s*(?P<ret>-?\d+)\s*$")

# 出现这些词的行通常就是失败原因，排在最前面。
FAILURE_HINT_RE = re.compile(
    r"(?i)(assert|fatal|fail|error|panic|abort|exception|traceback|"
    r"not\s+found|no\s+such|unsupported|not\s+supported|invalid|"
    r"timed?\s*out|timeout|killed|signal|segfault|core\s+dumped|"
    r"return:\s*-|errno|unreachable|cannot|can't|could\s+not|"
    r"end:\s*fail|TCONF|BROK)"
)


@dataclass
class PointBlock:
    """一个测试点在串口输出里占用的行范围。"""

    name: str
    lines: list[str] = field(default_factory=list)
    # 是否看到了配对的结束标记。False 表示输出被下一段截断（例如断言失败后
    # 直接跳到下一个测试点），这本身就是一条重要线索。
    terminated: bool = False


def split_point_blocks(body: str) -> dict[str, PointBlock]:
    """按四种段落标记把分组正文切成 ``测试点名字 -> 段落``。

    同名段落重复出现时保留最后一段（测试点被重新跑过时，最后一段才是结果）。
    """

    blocks: dict[str, PointBlock] = {}
    current: PointBlock | None = None

    def close(name: str) -> None:
        nonlocal current
        block = blocks.get(name)
        if block is None:
            block = blocks[name] = PointBlock(name=name)
        if current is not None and current is block:
            block.terminated = True
        current = None

    for raw in body.splitlines():
        line = raw.rstrip()

        match = _BLOCK_START.match(line)
        if match:
            name = match.group("name").strip()
            current = blocks[name] = PointBlock(name=name)
            continue

        match = _BLOCK_END.match(line)
        if match:
            name = match.group("name").strip()
            blocks.setdefault(name, PointBlock(name=name, terminated=True))
            close(name)
            continue

        match = _CASE_START.match(line)
        if match:
            name = match.group("name").strip()
            current = blocks[name] = PointBlock(name=name)
            continue

        match = _CASE_END.match(line)
        if match:
            name = match.group("name").strip()
            blocks.setdefault(name, PointBlock(name=name, terminated=True))
            close(name)
            continue

        match = _LUA_CASE.match(line)
        if match:
            name = match.group("name").strip()
            blocks[name] = PointBlock(name=name, lines=[line], terminated=True)
            current = None
            continue

        match = _LTP_START.match(line)
        if match:
            name = match.group("name").strip()
            current = blocks[name] = PointBlock(name=name)
            continue

        match = _LTP_END.match(line)
        if match:
            name = match.group("name").strip()
            block = blocks.get(name)
            if block is None:
                block = blocks[name] = PointBlock(name=name)
            block.terminated = True
            current = None
            continue

        if current is not None:
            current.lines.append(line)

    return blocks


def _trim(line: str) -> str:
    line = " ".join(line.split())
    if len(line) > MAX_LINE_CHARS:
        return line[: MAX_LINE_CHARS - 1] + "…"
    return line


def _ranked(lines: list[str]) -> list[str]:
    """失败线索优先，其次是段落的最后几行。"""

    useful = [_trim(line) for line in lines if line.strip()]
    if not useful:
        return []
    hits = [line for line in useful if FAILURE_HINT_RE.search(line)]
    if not hits:
        return useful[-2:]
    # 去掉重复，保留原始顺序（后面出现的通常是最终状态）。
    seen: set[str] = set()
    ordered: list[str] = []
    for line in hits:
        if line not in seen:
            seen.add(line)
            ordered.append(line)
    return ordered[-MAX_EVIDENCE_LINES:]


def _lookup(blocks: dict[str, PointBlock], name: str) -> PointBlock | None:
    block = blocks.get(name)
    if block is not None:
        return block
    lowered = name.strip().lower()
    for key, value in blocks.items():
        if key.strip().lower() == lowered:
            return value
    return None


def point_evidence(
    body: str,
    name: str,
    *,
    blocks: dict[str, PointBlock] | None = None,
    limit: int = MAX_EVIDENCE_LINES,
) -> tuple[list[str], bool]:
    """返回 ``(证据行, 段落是否被截断)``。

    名字在段落表里找不到时，退回「正文中包含该名字的行」，这样未知套件也
    不会得到空证据。
    """

    table = blocks if blocks is not None else split_point_blocks(body)
    block = _lookup(table, name)
    if block is not None:
        lines = _ranked(block.lines)[-limit:] if limit > 0 else []
        return lines, not block.terminated

    needle = name.strip()
    if not needle:
        return [], False
    fallback = [_trim(line) for line in body.splitlines() if needle in line]
    return fallback[-limit:] if limit > 0 else [], False
