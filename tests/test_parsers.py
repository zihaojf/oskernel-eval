"""Parser tests with realistic synthetic output.

Run with::

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starry_eval.model import GroupResult
from starry_eval.scoring.cyclictest import score_cyclictest
from starry_eval.scoring.libcbench import score_libcbench
from starry_eval.scoring.lmbench import score_lmbench
from starry_eval.scoring.ltp import score_ltp
from starry_eval.scoring.network import score_iperf, score_netperf
from starry_eval.scoring.unixbench import score_unixbench
from starry_eval.serial_parser import parse_serial


def make_group(suite: str, body: str) -> GroupResult:
    return GroupResult(
        group=f"{suite}-musl",
        suite=suite,
        variant="musl",
        complete=True,
        body=body,
    )


LMBENCH_BODY = """latency measurements
Simple syscall: 0.5 microseconds
Simple read: 1.0 microseconds
Simple write: 1.0 microseconds
Simple stat: 1.0 microseconds
Simple fstat: 1.0 microseconds
Simple open/close: 2.0 microseconds
Select on 100 fd's: 1.0 microseconds
Signal handler installation: 1.0 microseconds
Signal handler overhead: 1.0 microseconds
Protection fault: 1.0 microseconds
Pipe latency: 10.0 microseconds
Process fork+exit: 150.0 microseconds
Process fork+execve: 200.0 microseconds
Process fork+/bin/sh -c: 1000.0 microseconds
File /var/tmp/XXX write bandwidth: 50000 KB/sec
Pagefaults on /var/tmp/XXX: 1.0 microseconds
0.500000 5.0
file system latency
10k\t581\t150000\t100000
Bandwidth measurements
Pipe bandwidth: 5.00 MB/sec
0.500000 1.0
0.500000 1.2
0.500000 1.5
0.500000 1.8
context switch overhead
2 1.0
4 1.2
8 1.5
16 1.8
24 2.0
32 2.2
64 2.8
96 3.0
"""


class LmbenchTest(unittest.TestCase):
    def test_all_metrics(self) -> None:
        group = make_group("lmbench", LMBENCH_BODY)
        score_lmbench(group, {})
        self.assertEqual(len(group.points), 24, [p.name for p in group.points])
        values = {m.name: (m.value, m.unit) for m in group.metrics}
        self.assertEqual(values["lmbench/fs_write"][0], 50000.0)
        self.assertEqual(values["lmbench/fs_latency"][0], 250000.0)
        self.assertEqual(values["lmbench/pipe_bw"][0], 5120.0)
        self.assertEqual(values["lmbench/file_read_io"][0], 1.0)
        self.assertEqual(values["lmbench/mmap_read_only"][0], 1.5)
        self.assertEqual(values["lmbench/ctx_switch"][0], 3.0)
        self.assertGreater(group.score, 0.0)
        self.assertTrue(all(p.max_score == 1.0 for p in group.points))


class LtpTest(unittest.TestCase):
    def test_pass_fail_tconf(self) -> None:
        body = """RUN LTP CASE fork01
fork01 1 PASS Process forked
FAIL LTP CASE fork01 : 0
RUN LTP CASE open02
open02 1 FAIL EACCES
FAIL LTP CASE open02 : 1
RUN LTP CASE mmap03
mmap03 1 TCONF not supported
FAIL LTP CASE mmap03 : 32
"""
        group = make_group("ltp", body)
        score_ltp(group, {})
        self.assertEqual(len(group.points), 3)
        self.assertEqual(group.points[0].score, 1.0)
        self.assertEqual(group.points[1].score, 0.0)
        self.assertEqual(group.points[2].status, "skip")
        self.assertEqual(group.score, 1.0)
        self.assertEqual(group.max_score, 2.0)


NETPERF_BODY = """====== netperf UDP_STREAM begin ======
MIGRATED UDP STREAM TEST
Socket  Message  Elapsed      Messages
Size    Size     Time         Okay Errors   Throughput
bytes   bytes    secs            #      #   10^6bits/sec

 65536    1000   1.00        31013      0     247.98
 65536           1.00          650              5.20

====== netperf UDP_STREAM end: success ======
====== netperf TCP_STREAM begin ======
MIGRATED TCP STREAM TEST
 65536  65536   1000    0.01       45.22
====== netperf TCP_STREAM end: success ======
====== netperf UDP_RR begin ======
Local /Remote
Socket Size   Request  Resp.   Elapsed  Trans.
Send   Recv   Size     Size    Time     Rate
bytes  Bytes  bytes    bytes   secs.    per sec

65536  65536  64       64      1.00     1977.35
65536  65536
====== netperf UDP_RR end: success ======
====== netperf TCP_RR begin ======
65536  65536  64       64      1.00     2145.21
====== netperf TCP_RR end: success ======
====== netperf TCP_CRR begin ======
65536  65536  64       64      1.00      618.04
====== netperf TCP_CRR end: success ======
"""


class NetperfTest(unittest.TestCase):
    def test_all_cases(self) -> None:
        group = make_group("netperf", NETPERF_BODY)
        score_netperf(group, {})
        self.assertEqual(len(group.points), 5)
        self.assertEqual(group.score, 5.0)
        metrics = {m.name: m.value for m in group.metrics}
        self.assertEqual(metrics["netperf/UDP_STREAM sender"], 247.98)
        self.assertEqual(metrics["netperf/UDP_STREAM receiver"], 5.2)
        self.assertAlmostEqual(metrics["netperf/UDP_STREAM loss"], 97.903, places=3)
        self.assertEqual(metrics["netperf/TCP_STREAM throughput"], 45.22)
        self.assertEqual(metrics["netperf/TCP_RR trans_rate"], 2145.21)
        self.assertTrue(
            any("receiver throughput" in w for w in group.warnings),
            group.warnings,
        )


IPERF_BODY = """====== iperf BASIC_UDP begin ======
[  5]   0.00-2.00   sec  36.9 MBytes   155 Mbits/sec  26533
[  5]   0.00-2.04   sec   878 KBytes  3.52 Mbits/sec  0.241 ms  23091/23707 (97%)  receiver
====== iperf BASIC_UDP end: success ======
====== iperf BASIC_TCP begin ======
[  5]   0.00-2.00   sec   102 MBytes   428 Mbits/sec  774909488    256 TBytes
[  5]   0.00-2.00   sec   102 MBytes   427 Mbits/sec                  receiver
====== iperf BASIC_TCP end: success ======
====== iperf PARALLEL_UDP begin ======
[SUM]   0.00-2.00   sec  51.9 MBytes   218 Mbits/sec  37285
[SUM]   0.00-2.05   sec   878 KBytes  3.52 Mbits/sec  10.451 ms  34673/35289 (93%)  receiver
====== iperf PARALLEL_UDP end: success ======
====== iperf PARALLEL_TCP begin ======
[SUM]   0.00-2.00   sec  96.2 MBytes   403 Mbits/sec  -420419856
====== iperf PARALLEL_TCP end: success ======
====== iperf REVERSE_UDP begin ======
[  5]   0.00-2.00   sec  36.9 MBytes   155 Mbits/sec  26533
[  5]   0.00-2.04   sec   878 KBytes  3.52 Mbits/sec  0.241 ms  23091/23707 (97%)  receiver
====== iperf REVERSE_UDP end: success ======
====== iperf REVERSE_TCP begin ======
[  5]   0.00-2.00   sec   102 MBytes   428 Mbits/sec  1768049952
====== iperf REVERSE_TCP end: success ======
"""


class IperfTest(unittest.TestCase):
    def test_all_cases_and_traffic(self) -> None:
        group = make_group("iperf", IPERF_BODY)
        score_iperf(group, {})
        self.assertEqual(len(group.points), 6)
        self.assertEqual(group.score, 6.0)
        metrics = {m.name: m.value for m in group.metrics}
        self.assertAlmostEqual(metrics["iperf/BASIC_UDP receiver"], 3.52)
        self.assertAlmostEqual(metrics["iperf/BASIC_UDP udp_loss"], 97.0)
        self.assertGreater(metrics["iperf/BASIC_TCP tcp_retrans"], 1e8)
        self.assertTrue(any("UDP loss" in w for w in group.warnings))
        self.assertTrue(any("retransmit counter" in w for w in group.warnings))


CYCLICTEST_BODY = """====== cyclictest NO_STRESS_P1 begin ======
T: 0 ( 1) P:99 I:1000 C: 1000 Min: 1 Act: 1 Avg: 2 Max: 5
====== cyclictest NO_STRESS_P1 end: success ======
====== cyclictest NO_STRESS_P8 begin ======
T: 0 ( 2) P:99 I:1000 C: 1000 Min: 1 Act: 1 Avg: 2 Max: 6
====== cyclictest NO_STRESS_P8 end: success ======
====== cyclictest STRESS_P1 begin ======
T: 0 ( 3) P:99 I:1000 C: 1000 Min: 1 Act: 1 Avg: 2000 Max: 9000
====== cyclictest STRESS_P1 end: success ======
====== cyclictest STRESS_P8 begin ======
T: 0 ( 4) P:99 I:1000 C: 1000 Min: 1 Act: 1 Avg: 3000 Max: 12000
====== cyclictest STRESS_P8 end: success ======
====== kill hackbench: fail ======
"""


class CyclictestTest(unittest.TestCase):
    def test_stress_discarded(self) -> None:
        group = make_group("cyclictest", CYCLICTEST_BODY)
        score_cyclictest(group, {})
        self.assertEqual(len(group.points), 4)
        self.assertEqual(group.points[0].score, 1.0)
        self.assertEqual(group.points[2].score, 0.0)
        self.assertEqual(group.score, 2.0)
        self.assertTrue(any("discarded" in w for w in group.warnings))


LIBCBENCH_BODY = """
b_malloc_sparse (0)
  time: 0.1, virt: 0, res: 0, dirty: 0

b_string_memset (0)
  time: 0.2, virt: 0, res: 0, dirty: 0
"""


class LibcbenchTest(unittest.TestCase):
    def test_partial_coverage(self) -> None:
        group = make_group("libcbench", LIBCBENCH_BODY)
        score_libcbench(group, {})
        self.assertEqual(group.score, 2.0)
        self.assertEqual(group.max_score, 27.0)
        self.assertEqual(len(group.metrics), 2)


UNIXBENCH_BODY = """
Unixbench DHRY2 test(lps): 123
Unixbench WHETSTONE test(MFLOPS): 4.5
"""


class UnixbenchTest(unittest.TestCase):
    def test_partial_coverage(self) -> None:
        group = make_group("unixbench", UNIXBENCH_BODY)
        score_unixbench(group, {})
        self.assertEqual(group.score, 2.0)
        self.assertEqual(group.max_score, 27.0)
        self.assertEqual(len(group.metrics), 2)


SERIAL_BODY = """boot log
#### StarryOS: running /musl/basic_testcode.sh ####
#### OS COMP TEST GROUP START basic-musl ####
hello
#### OS COMP TEST GROUP END basic-musl ####
#### StarryOS: finished /musl/basic_testcode.sh (status 0) ####
#### OS COMP TEST GROUP START lua-musl ####
partial output
#### StarryOS: running /glibc/lua_testcode.sh ####
#### OS COMP TEST GROUP END lua-musl ####
"""


class SerialParserTest(unittest.TestCase):
    def test_groups_and_markers(self) -> None:
        parsed = parse_serial(SERIAL_BODY)
        self.assertEqual(len(parsed.groups), 2)
        self.assertTrue(parsed.groups[0].complete)
        self.assertEqual(parsed.groups[0].group, "basic-musl")
        self.assertEqual(parsed.groups[0].body.strip(), "hello")
        self.assertFalse(parsed.groups[1].complete)
        self.assertEqual(len(parsed.script_runs), 2)


if __name__ == "__main__":
    unittest.main()
