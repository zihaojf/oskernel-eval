"""Competition metadata and reference values.

The values in this file come from
``~/starry-work/oskernel-testsuits-cooperation`` (doc/*.md and judge/*.py).
They are deliberately kept as data so that the parser code stays small and
reviewable.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Suite catalogue
# ---------------------------------------------------------------------------

MODE_OFFICIAL = "official"  # score from the provided judge_*.py
MODE_RANGE = "range"  # score from a documented expected range (lmbench)
MODE_COVERAGE = "coverage"  # one point per benchmark that produced output
MODE_SUCCESS = "success"  # one point per test case that reported success
MODE_PASSRATE = "passrate"  # one point per passed case (ltp)


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    title: str
    mode: str
    kind: str  # functional | performance | mixed
    default_max: float | None
    expected_metrics: int | None = None
    official_judge: bool = False
    notes: str = ""


# Competition order (kept for reports).
SUITE_ORDER = (
    "basic",
    "busybox",
    "lua",
    "libctest",
    "iozone",
    "unixbench",
    "iperf",
    "libcbench",
    "lmbench",
    "netperf",
    "cyclictest",
    "ltp",
)

SUITE_SPECS: dict[str, SuiteSpec] = {
    "basic": SuiteSpec(
        name="basic",
        title="basic 系统调用",
        mode=MODE_OFFICIAL,
        kind="functional",
        default_max=102,
        official_judge=True,
        notes="judge_basic.py: one point per assertion",
    ),
    "busybox": SuiteSpec(
        name="busybox",
        title="busybox 命令集",
        mode=MODE_OFFICIAL,
        kind="functional",
        default_max=55,
        official_judge=True,
        notes="one point per command; 55 with the two marker commands",
    ),
    "lua": SuiteSpec(
        name="lua",
        title="lua 解释器",
        mode=MODE_OFFICIAL,
        kind="functional",
        default_max=9,
        official_judge=True,
        notes="9 scripts, one point each",
    ),
    "libctest": SuiteSpec(
        name="libctest",
        title="libc-test 测试集",
        mode=MODE_OFFICIAL,
        kind="functional",
        default_max=220,
        official_judge=True,
        notes="220 static+dynamic cases, one point each",
    ),
    "iozone": SuiteSpec(
        name="iozone",
        title="iozone 文件 I/O 吞吐",
        mode=MODE_OFFICIAL,
        kind="performance",
        default_max=40,
        expected_metrics=20,
        official_judge=True,
        notes="judge_iozone.py: 20 metrics x up to 2 points",
    ),
    "unixbench": SuiteSpec(
        name="unixbench",
        title="UnixBench 综合性能",
        mode=MODE_COVERAGE,
        kind="performance",
        default_max=27,
        expected_metrics=27,
        notes="27 benchmarks; score is coverage, metrics are compared separately",
    ),
    "iperf": SuiteSpec(
        name="iperf",
        title="iperf3 网络吞吐",
        mode=MODE_SUCCESS,
        kind="mixed",
        default_max=6,
        expected_metrics=6,
        notes="6 cases (basic/parallel/reverse x UDP/TCP)",
    ),
    "libcbench": SuiteSpec(
        name="libcbench",
        title="libc-bench 库函数性能",
        mode=MODE_COVERAGE,
        kind="performance",
        default_max=27,
        expected_metrics=27,
        notes="27 benchmarks; score is coverage, metrics are compared separately",
    ),
    "lmbench": SuiteSpec(
        name="lmbench",
        title="lmbench 微基准",
        mode=MODE_RANGE,
        kind="performance",
        default_max=24,
        expected_metrics=24,
        notes="24 metrics; score is the normalized position in the expected range",
    ),
    "netperf": SuiteSpec(
        name="netperf",
        title="netperf 网络事务",
        mode=MODE_SUCCESS,
        kind="mixed",
        default_max=5,
        expected_metrics=5,
        notes="5 cases (UDP/TCP stream and request/response)",
    ),
    "cyclictest": SuiteSpec(
        name="cyclictest",
        title="cyclictest 实时延迟 + hackbench",
        mode=MODE_SUCCESS,
        kind="mixed",
        default_max=4,
        expected_metrics=4,
        notes="4 runs; stress runs discarded if hackbench cannot be killed",
    ),
    "ltp": SuiteSpec(
        name="ltp",
        title="Linux Test Project（ltp）",
        mode=MODE_PASSRATE,
        kind="functional",
        default_max=None,
        notes="pass rate; TCONF is excluded from the denominator",
    ),
}


# A suite is "official" when the provided judge scripts can produce the score.
# These are the suites whose totals can be compared directly with the online
# evaluation (if it uses the same judge revision).
OFFICIAL_SUITES = tuple(n for n, s in SUITE_SPECS.items() if s.official_judge)

# Suites that are useful for the composite local score.  ltp is excluded by
# default because its denominator (2822 binaries in the shipped image) makes a
# single number meaningless.
COMPOSITE_SUITES = tuple(n for n in SUITE_ORDER if n != "ltp")


# ---------------------------------------------------------------------------
# libc-bench
# ---------------------------------------------------------------------------

LIBCTEST_EXPECTED = 220

# Names printed by libc-bench.  Only used to report missing / unexpected rows.
LIBCBENCH_EXPECTED = (
    "b_malloc_sparse",
    "b_malloc_bubble",
    "b_malloc_tiny1",
    "b_malloc_tiny2",
    "b_malloc_big1",
    "b_malloc_big2",
    "b_malloc_thread_stress",
    "b_malloc_thread_local",
    "b_string_strstr",
    "b_string_memset",
    "b_string_strchr",
    "b_string_strlen",
    "b_pthread_createjoin_serial1",
    "b_pthread_createjoin_serial2",
    "b_pthread_create_serial1",
    "b_pthread_uselesslock",
    "b_utf8_bigbuf",
    "b_utf8_onebyone",
    "b_stdio_putcgetc",
    "b_stdio_putcgetc_unlocked",
    "b_regex_compile",
    "b_regex_search",
)


# ---------------------------------------------------------------------------
# UnixBench
# ---------------------------------------------------------------------------

UNIXBENCH_EXPECTED = (
    "DHRY2",
    "WHETSTONE",
    "SYSCALL",
    "CONTEXT",
    "PIPE",
    "SPAWN",
    "EXECL",
    "FS_WRITE_SMALL",
    "FS_READ_SMALL",
    "FS_COPY_SMALL",
    "FS_WRITE_MIDDLE",
    "FS_READ_MIDDLE",
    "FS_COPY_MIDDLE",
    "FS_WRITE_BIG",
    "FS_READ_BIG",
    "FS_COPY_BIG",
    "SHELL1",
    "SHELL8",
    "SHELL16",
    "ARITHOH",
    "SHORT",
    "INT",
    "LONG",
    "FLOAT",
    "DOUBLE",
    "HANOI",
    "EXEC",
)

UNIXBENCH_UNITS = {
    "WHETSTONE": "MFLOPS",
    "FS_WRITE_SMALL": "KBps",
    "FS_READ_SMALL": "KBps",
    "FS_COPY_SMALL": "KBps",
    "FS_WRITE_MIDDLE": "KBps",
    "FS_READ_MIDDLE": "KBps",
    "FS_COPY_MIDDLE": "KBps",
    "FS_WRITE_BIG": "KBps",
    "FS_READ_BIG": "KBps",
    "FS_COPY_BIG": "KBps",
    "SHELL1": "lpm",
    "SHELL8": "lpm",
    "SHELL16": "lpm",
}


# ---------------------------------------------------------------------------
# iperf / netperf / cyclictest
# ---------------------------------------------------------------------------

IPERF_CASES = (
    "BASIC_UDP",
    "BASIC_TCP",
    "PARALLEL_UDP",
    "PARALLEL_TCP",
    "REVERSE_UDP",
    "REVERSE_TCP",
)

NETPERF_CASES = ("UDP_STREAM", "TCP_STREAM", "UDP_RR", "TCP_RR", "TCP_CRR")

CYCLICTEST_CASES = ("NO_STRESS_P1", "NO_STRESS_P8", "STRESS_P1", "STRESS_P8")

# Above this one-way UDP loss the run is reported as degraded even when iperf
# exits with status 0.
UDP_LOSS_WARN_PCT = 5.0

# A single TCP retransmission counter larger than this is almost certainly a
# kernel bug rather than real congestion on loopback.
TCP_RETRANS_WARN = 1_000_000

# cyclictest latency thresholds (microseconds).  The competition sample output
# shows single-digit to low-double-digit maxima on a normal kernel.  A run with
# a maximum above CYCLIC_SCORE_MAX_US gets 0 run-points: the online table shows
# 0/4 for the current StarryOS, whose maxima are around 10 ms, and the scoring
# basis says the real-time behaviour is part of the score.
CYCLIC_MAX_WARN_US = 1000.0
CYCLIC_MAX_BAD_US = 5000.0
CYCLIC_SCORE_MAX_US = 1000.0


# ---------------------------------------------------------------------------
# lmbench
# ---------------------------------------------------------------------------

LMBENCH_MODE = {
    "lower": "lower",
    "higher": "higher",
}


@dataclass(frozen=True)
class LmbenchSpec:
    index: int
    key: str
    title: str
    unit: str
    direction: str
    low: float
    high: float
    patterns: tuple[str, ...] = ()
    nth: int = 1
    note: str = ""


# The expected ranges are copied from doc/lmbench.md (low = best end, high =
# worst end).  "lower" means smaller is better, "higher" means larger is
# better.
LMBENCH_SPECS: tuple[LmbenchSpec, ...] = (
    LmbenchSpec(1, "null", "空系统调用延迟", "us", "lower", 0.0757, 2.0,
                (r"Simple syscall:\s*([0-9.]+)", r"null(?: syscall)?:\s*([0-9.]+)")),
    LmbenchSpec(2, "read", "读系统调用延迟", "us", "lower", 0.1, 2.2,
                (r"Simple read:\s*([0-9.]+)", r"read latency:\s*([0-9.]+)")),
    LmbenchSpec(3, "write", "写系统调用延迟", "us", "lower", 0.1, 2.2,
                (r"Simple write:\s*([0-9.]+)", r"write latency:\s*([0-9.]+)")),
    LmbenchSpec(4, "stat", "stat 系统调用延迟", "us", "lower", 0.5, 2.1,
                (r"Simple stat:\s*([0-9.]+)", r"stat latency:\s*([0-9.]+)")),
    LmbenchSpec(5, "fstat", "fstat 系统调用延迟", "us", "lower", 0.15, 2.0,
                (r"Simple fstat:\s*([0-9.]+)", r"fstat latency:\s*([0-9.]+)")),
    LmbenchSpec(6, "open", "open 系统调用延迟", "us", "lower", 1.0, 3.0,
                (r"Simple open/close:\s*([0-9.]+)", r"Simple open:\s*([0-9.]+)",
                 r"open/close:\s*([0-9.]+)")),
    LmbenchSpec(7, "select", "select 系统调用延迟", "us", "lower", 0.5, 2.7,
                (r"Select on \d+ fd'?s:\s*([0-9.]+)", r"select.*?:\s*([0-9.]+)")),
    LmbenchSpec(8, "sig_install", "信号安装延迟", "us", "lower", 0.1, 2.3,
                (r"Signal handler installation:\s*([0-9.]+)",)),
    LmbenchSpec(9, "sig_catch", "信号捕获延迟", "us", "lower", 0.8, 2.0,
                (r"Signal handler overhead:\s*([0-9.]+)",)),
    LmbenchSpec(10, "sig_prot", "信号保护延迟", "us", "lower", 0.3, 2.5,
                (r"Protection fault:\s*([0-9.]+)",)),
    LmbenchSpec(11, "pipe", "管道延迟", "us", "lower", 4.0, 20.0,
                (r"Pipe latency:\s*([0-9.]+)",)),
    LmbenchSpec(12, "fork", "进程 fork 延迟", "us", "lower", 94.0, 200.0,
                (r"Process fork\+exit:\s*([0-9.]+)", r"fork\+exit:\s*([0-9.]+)")),
    LmbenchSpec(13, "exec", "进程 exec 延迟", "us", "lower", 101.0, 300.0,
                (r"Process fork\+execve:\s*([0-9.]+)", r"fork\+execve:\s*([0-9.]+)")),
    LmbenchSpec(14, "shell", "进程 shell 延迟", "us", "lower", 300.0, 2000.0,
                (r"Process fork\+/bin/sh -c:\s*([0-9.]+)",
                 r"Process fork\+/bin/sh:\s*([0-9.]+)")),
    LmbenchSpec(15, "fs_write", "文件系统写入带宽", "KB/sec", "higher", 3000.0, 200000.0,
                (r"File .* write bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec",)),
    LmbenchSpec(16, "pagefault", "页面错误延迟", "us", "lower", 0.1, 3.2,
                (r"Pagefaults? on .*?:\s*([0-9.]+)",)),
    LmbenchSpec(17, "mmap", "mmap 延迟", "us", "lower", 0.3, 10.0,
                (r"(?:512k )?mmap latency:\s*([0-9.]+)", r"mmap.*?latency:\s*([0-9.]+)")),
    LmbenchSpec(18, "pipe_bw", "管道带宽", "KB/sec", "higher", 1000.0, 10000.0,
                (r"Pipe bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec",)),
    LmbenchSpec(19, "fs_latency", "文件系统延迟", "creations+deletes/sec", "higher",
                100000.0, 300000.0, (), note="lat_fs: create+delete rates"),
    LmbenchSpec(20, "file_read_io", "512k 文件读取速度（仅 I/O）", "MB/sec", "higher",
                0.5, 2.0, (r"512k read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec",
                           r"File read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec"),
                nth=1),
    LmbenchSpec(21, "file_read_o2c", "512k 文件读取速度（打开至关闭）", "MB/sec",
                "higher", 0.5, 2.2, (r"512k read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec",
                                      r"File read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec"),
                nth=2),
    LmbenchSpec(22, "mmap_read_only", "512k mmap 读取速度（mmaponly）", "MB/sec",
                "higher", 0.5, 3.0, (r"512k mmap read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec",
                                      r"mmap read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec"),
                nth=1),
    LmbenchSpec(23, "mmap_read_o2c", "512k mmap 读取速度（open2close）", "MB/sec",
                "higher", 0.5, 3.0, (r"512k mmap read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec",
                                      r"mmap read bandwidth:\s*([0-9.]+)\s*(KB|MB)/sec"),
                nth=2),
    LmbenchSpec(24, "ctx_switch", "上下文切换延迟（96 进程）", "us", "lower", 1.0, 5.0,
                (r"(?:^|\n)\s*96:\s*([0-9.]+)",)),
)


# ---------------------------------------------------------------------------
# ltp
# ---------------------------------------------------------------------------

# ltp is intentionally not part of the composite total.  This is the number of
# executables in the shipped image's ltp/testcases/bin directory; it is only
# used to display progress and to flag an incomplete run.
LTP_SHIPPED_CASES = 2822
