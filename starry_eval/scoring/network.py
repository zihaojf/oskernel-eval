"""iperf3 and netperf parsers.

Both tools report success through the test script even when the actual traffic
is degraded (e.g. 97 % UDP loss).  The parser therefore keeps two things apart:

* the case point, which follows the ``end: success/fail`` marker exactly like
  the competition script, and
* the traffic metrics, which raise warnings when loopback traffic is broken.
"""

from __future__ import annotations

import re

from ..model import GroupResult, Metric, TestPoint, STATUS_FAIL, STATUS_FULL
from ..references import (
    IPERF_CASES,
    NETPERF_CASES,
    TCP_RETRANS_WARN,
    UDP_LOSS_WARN_PCT,
)
from .common import add_metric, find_status_sections, to_float

# ---------------------------------------------------------------------------
# iperf3
# ---------------------------------------------------------------------------

IPERF_START_RE = re.compile(r"^======\s*iperf\s+(.+?)\s+begin\s*======$")
IPERF_END_RE = re.compile(
    r"^======\s*iperf\s+(.+?)\s+end:\s*(success|fail)\s*======$"
)

# [  5]   0.00-2.00   sec  36.9 MBytes   155 Mbits/sec  26533
# [SUM]   0.00-2.05   sec   878 KBytes  3.52 Mbits/sec  0.241 ms  23091/23707 (97%)  receiver
IPERF_LINE_RE = re.compile(
    r"^\[(?P<id>SUM|\s*\d+)\]\s+"
    r"(?P<t0>[0-9.]+)-(?P<t1>[0-9.]+)\s+sec\s+"
    r"(?P<bytes>[0-9.]+)\s+(?P<bunit>[KMG])Bytes\s+"
    r"(?P<rate>[0-9.]+)\s+(?P<runit>[KMG])bits/sec"
    r"(?P<rest>.*)$"
)
IPERF_LOSS_RE = re.compile(r"(\d+)/(\d+)\s*\(([0-9.]+)%\)")

_RATE_TO_MBITS = {"K": 1.0 / 1000.0, "M": 1.0, "G": 1000.0}


def _parse_iperf_traffic(
    text: str, case: str, metrics: list[Metric], baseline: dict[str, float]
) -> None:
    is_tcp = "TCP" in case.upper()
    sender_bw: float | None = None
    receiver_bw: float | None = None
    loss_pct: float | None = None
    retrans: int | None = None

    for line in text.splitlines():
        match = IPERF_LINE_RE.match(line.strip())
        if not match:
            continue
        rate = to_float(match.group("rate"))
        rate_mbits = rate * _RATE_TO_MBITS.get(match.group("runit"), 1.0)
        role = "receiver" if "receiver" in line else "sender" if "sender" in line else ""
        if role == "receiver" and receiver_bw is None:
            receiver_bw = rate_mbits
        elif role == "sender" and sender_bw is None:
            sender_bw = rate_mbits
        elif role == "" and sender_bw is None:
            sender_bw = rate_mbits

        if is_tcp:
            # The retransmission counter is the first integer after the bitrate
            # on the sender line.  A value such as 774909488 is a kernel bug,
            # not real congestion on loopback.
            rest = match.group("rest").strip()
            first = re.match(r"^(\d+)\b", rest)
            if first and "receiver" not in line:
                retrans = int(first.group(1))

        loss = IPERF_LOSS_RE.search(line)
        if loss:
            loss_pct = to_float(loss.group(3))

    if sender_bw is None and receiver_bw is None:
        return

    if sender_bw is not None:
        add_metric(
            metrics,
            name=f"iperf/{case} sender",
            value=sender_bw,
            unit="Mbits/sec",
            direction="higher",
            baseline=baseline.get(f"iperf/{case} sender"),
        )
    if receiver_bw is not None:
        add_metric(
            metrics,
            name=f"iperf/{case} receiver",
            value=receiver_bw,
            unit="Mbits/sec",
            direction="higher",
            baseline=baseline.get(f"iperf/{case} receiver"),
        )
    if loss_pct is not None:
        add_metric(
            metrics,
            name=f"iperf/{case} udp_loss",
            value=loss_pct,
            unit="%",
            direction="lower",
            range_low=0.0,
            range_high=100.0,
            baseline=baseline.get(f"iperf/{case} udp_loss"),
        )
    if retrans is not None:
        add_metric(
            metrics,
            name=f"iperf/{case} tcp_retrans",
            value=float(retrans),
            unit="count",
            direction="lower",
            range_low=0.0,
            range_high=float(TCP_RETRANS_WARN),
            baseline=baseline.get(f"iperf/{case} tcp_retrans"),
        )


def score_iperf(group: GroupResult, baseline: dict[str, float]) -> None:
    sections = find_status_sections(group.body, IPERF_START_RE, IPERF_END_RE)
    points: list[TestPoint] = []
    seen: list[str] = []

    for name, text, complete, status in sections:
        seen.append(name)
        ok = complete and status == "success"
        points.append(
            TestPoint(
                name=name,
                score=1.0 if ok else 0.0,
                max_score=1.0,
                status=STATUS_FULL if ok else STATUS_FAIL,
                kind="mixed",
                note=f"end: {status or 'missing'}",
            )
        )
        if not complete:
            group.warnings.append(f"iperf {name}: section has no end marker")
        if not ok:
            group.warnings.append(f"iperf {name}: reported {status or 'incomplete'}")

        _parse_iperf_traffic(text, name, group.metrics, baseline)
        loss = next(
            (m for m in group.metrics if m.name == f"iperf/{name} udp_loss"), None
        )
        if loss is not None and loss.value > UDP_LOSS_WARN_PCT:
            group.warnings.append(
                f"iperf {name}: {loss.value:.1f}% UDP loss "
                f"(sender data is not reaching the guest)"
            )
        retrans = next(
            (m for m in group.metrics if m.name == f"iperf/{name} tcp_retrans"), None
        )
        if retrans is not None and retrans.value > TCP_RETRANS_WARN:
            group.warnings.append(
                f"iperf {name}: suspicious TCP retransmit counter "
                f"{retrans.value:.0f}"
            )

    missing = [name for name in IPERF_CASES if name not in seen]
    if missing:
        points.append(
            TestPoint(
                name="iperf/missing-cases",
                score=0.0,
                max_score=float(len(missing)),
                status=STATUS_FAIL,
                kind="mixed",
                note="missing: " + ", ".join(missing),
            )
        )

    group.points.extend(points)


# ---------------------------------------------------------------------------
# netperf
# ---------------------------------------------------------------------------

NETPERF_START_RE = re.compile(r"^======\s*netperf\s+(.+?)\s+begin\s*======$")
NETPERF_END_RE = re.compile(
    r"^======\s*netperf\s+(.+?)\s+end:\s*(success|fail)\s*======$"
)

# netperf stream tables:
#   TCP:  recv_socket send_socket msg_size elapsed throughput          (5 cols)
#   UDP:  socket msg_size elapsed okay errors throughput               (6 cols)
#   UDP:  socket elapsed okay throughput                               (4 cols)
STREAM_6_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+([0-9.]+)\s+(\d+)\s+(\d+)\s+([0-9.]+)\s*$"
)
STREAM_5_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+([0-9.]+)\s+([0-9.]+)\s*$"
)
STREAM_4_RE = re.compile(
    r"^\s*(\d+)\s+([0-9.]+)\s+(\d+)\s+([0-9.]+)\s*$"
)
# Request/response: send_socket recv_socket req_size resp_size elapsed rate
RR_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([0-9.]+)\s+([0-9.]+)\s*$"
)


def _rows(regex: re.Pattern[str], text: str) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for line in text.splitlines():
        match = regex.match(line)
        if match:
            rows.append(match.groups())
    return rows


def _parse_netperf_traffic(
    text: str, case: str, metrics: list[Metric], baseline: dict[str, float]
) -> None:
    if case == "UDP_STREAM":
        senders = _rows(STREAM_6_RE, text)
        receivers = _rows(STREAM_4_RE, text)
        sender = to_float(senders[-1][5]) if senders else None
        receiver = to_float(receivers[-1][3]) if receivers else None
        if sender is not None:
            add_metric(
                metrics,
                name=f"netperf/{case} sender",
                value=sender,
                unit="Mbits/sec",
                direction="higher",
                baseline=baseline.get(f"netperf/{case} sender"),
            )
        if receiver is not None:
            add_metric(
                metrics,
                name=f"netperf/{case} receiver",
                value=receiver,
                unit="Mbits/sec",
                direction="higher",
                baseline=baseline.get(f"netperf/{case} receiver"),
            )
        if sender and receiver is not None:
            loss_pct = max(0.0, (1.0 - receiver / sender) * 100.0)
            add_metric(
                metrics,
                name=f"netperf/{case} loss",
                value=loss_pct,
                unit="%",
                direction="lower",
                range_low=0.0,
                range_high=100.0,
                baseline=baseline.get(f"netperf/{case} loss"),
            )
        return

    if case == "TCP_STREAM":
        rows = _rows(STREAM_5_RE, text)
        if rows:
            add_metric(
                metrics,
                name=f"netperf/{case} throughput",
                value=to_float(rows[-1][4]),
                unit="Mbits/sec",
                direction="higher",
                baseline=baseline.get(f"netperf/{case} throughput"),
            )
        return

    rows = _rows(RR_RE, text)
    if rows:
        add_metric(
            metrics,
            name=f"netperf/{case} trans_rate",
            value=to_float(rows[-1][5]),
            unit="trans/sec",
            direction="higher",
            baseline=baseline.get(f"netperf/{case} trans_rate"),
        )


def score_netperf(group: GroupResult, baseline: dict[str, float]) -> None:
    sections = find_status_sections(group.body, NETPERF_START_RE, NETPERF_END_RE)
    points: list[TestPoint] = []
    seen: list[str] = []

    for name, text, complete, status in sections:
        seen.append(name)
        ok = complete and status == "success"
        points.append(
            TestPoint(
                name=name,
                score=1.0 if ok else 0.0,
                max_score=1.0,
                status=STATUS_FULL if ok else STATUS_FAIL,
                kind="mixed",
                note=f"end: {status or 'missing'}",
            )
        )
        if not complete:
            group.warnings.append(f"netperf {name}: section has no end marker")
        if not ok:
            group.warnings.append(f"netperf {name}: reported {status or 'incomplete'}")

        _parse_netperf_traffic(text, name, group.metrics, baseline)
        if name == "UDP_STREAM":
            values = {metric.name: metric.value for metric in group.metrics}
            sender = values.get("netperf/UDP_STREAM sender")
            receiver = values.get("netperf/UDP_STREAM receiver")
            if sender and receiver is not None and receiver < sender * 0.5:
                group.warnings.append(
                    f"netperf UDP_STREAM: receiver throughput is "
                    f"{receiver / sender * 100:.1f}% of sender"
                )

    if "so_dontroute" in group.body or "enable_enobufs" in group.body:
        group.warnings.append(
            "netperf setsockopt(SO_DONTROUTE/SO_ENOBUFS) is not supported; "
            "the test still runs but the setup is degraded"
        )

    missing = [name for name in NETPERF_CASES if name not in seen]
    if missing:
        points.append(
            TestPoint(
                name="netperf/missing-cases",
                score=0.0,
                max_score=float(len(missing)),
                status=STATUS_FAIL,
                kind="mixed",
                note="missing: " + ", ".join(missing),
            )
        )

    group.points.extend(points)
