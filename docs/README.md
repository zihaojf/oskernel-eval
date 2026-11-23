# 测试点文档（上游原文副本）

队友最常问的两个问题：**这个测试点到底在测什么？分数怎么算？**
答案都在这里。这些文件是 `oscomp/oskernel-testsuits-cooperation` 的
`doc/` 与 `lmbench/` 的**原文副本，未做任何修改**（来源见
[NOTICE.md](NOTICE.md)）。

> **注意**：上游文档里个别链接写成 `../../judge/judge_xxx.py`，在本仓库里
> 对应的文件是 [`../vendor/judge/`](../vendor/judge/)（judge 脚本已被本工具
> 内置）。文档本身保持上游原样、未作修改，这里只说明落点。

本工具自己的评分实现不在这些文档里，在 [`../starry_eval/scoring/`](../starry_eval/scoring/)：

- 有官方 `judge_*.py` 的套件 → 逐字执行官方脚本（`scoring/official.py`），
  分数就是在线评测会给的分；
- 其余套件 → 按文档里的公式/阈值本地实现（`scoring/*.py`）。

---

## 速查：suite → 文档 → 评分方式

| suite | 在测什么 | 文档 | 满分 | 本工具怎么算 |
|---|---|---|---:|---|
| `basic` | 基础 Linux syscall 的行为是否正确（brk、chdir、clone、dup2、mount…） | [basic/basic.md](basic/basic.md)、[basic/oscomp_syscalls.md](basic/oscomp_syscalls.md) | 102 | 官方 `judge_basic.py`，断言逐条计分 |
| `busybox` | busybox 各命令（ls、cat、grep、kill…）在你的 OS 上能否正常跑 | [busybox/busybox.md](busybox/busybox.md)、[busybox/busybox_cmd.txt](busybox/busybox_cmd.txt) | 55 | 官方 `judge_busybox.py`，每条命令 1 分 |
| `lua` | lua 解释器能否完整跑通测试脚本 | [lua/lua.md](lua/lua.md)、[lua/lua_testcode.sh](lua/lua_testcode.sh) | 9 | 官方 `judge_lua.py`，每个脚本 1 分 |
| `libctest` | C 标准库静态/动态用例（printf、regex、pthread、math…） | [libctest.md](libctest.md) | 220 | 官方 `judge_libctest.py`，每个 case 1 分 |
| `iozone` | 文件系统读写吞吐（4 readers/writers、pwrite/pread…） | [../starry_eval/scoring/iozone.py](../starry_eval/scoring/iozone.py) + 官方 `judge_iozone.py` | 40 | 官方 `judge_iozone.py`：达到基线 1 分，越快越接近 2 分 |
| `unixbench` | 综合性能（dhry2、whet、pipe、shell、syscall…） | [Unixbench.md](Unixbench.md) | 27 | 本地 `scoring/unixbench.py`：覆盖率 + 指标 |
| `iperf` | TCP/UDP 带宽、丢包、重传 | [iperf/iperf.md](iperf/iperf.md) | 6 | 本地 `scoring/network.py` |
| `libcbench` | libc 函数性能（malloc、stdio、pthread、regex…） | [libcbench/libcbench.md](libcbench/libcbench.md)、[libcbench/libcbench_testcode.sh](libcbench/libcbench_testcode.sh) | 27 | 本地 `scoring/libcbench.py`：覆盖率 + 时间指标 |
| `lmbench` | 微基准：系统调用延迟、内存/文件带宽、上下文切换 | [lmbench.md](lmbench.md)（指标与预期范围）、[lmbench/lmbench.md](lmbench/lmbench.md)（原理与题目描述） | 24 | 本地 `scoring/lmbench.py`：24 指标 + 区间质量 |
| `netperf` | 网络收发吞吐与请求响应 | [../starry_eval/scoring/network.py](../starry_eval/scoring/network.py) | 5 | 本地 `scoring/network.py` |
| `cyclictest` | 实时性：调度延迟（含 hackbench 负载） | [cyclictest/cyclictest.md](cyclictest/cyclictest.md) | 4 | 本地 `scoring/cyclictest.py`：延迟阈值 + STRESS 作废规则 |
| `ltp` | Linux 内核 syscall 兼容性（2822 个 case，通过率） | [ltp/ltp.md](ltp/ltp.md) | 动态 | 本地 `scoring/ltp.py`：PASS/FAIL/TCONF 通过率，不进 composite |

`lmbench.md` 和 `lmbench/lmbench.md` 是**两份不同的文档**（不是重复）：
前者是每个指标的预期区间（评分阈值就来自这里），后者讲 lmbench 是什么、
怎么设计。查分数看前者，看原理看后者。

---

## 官方评分协议（`judge_*.py` 是如何被调用的）

见 [上游 judge/README.md](../vendor/judge/README.md) 的完整说明，要点：

1. 在线评测读到串口里成对的
   `#### OS COMP TEST GROUP START <suite> ####` /
   `#### OS COMP TEST GROUP END <suite> ####`，把两行之间的文本抽出来；
2. 用 stdin 喂给同名的 `judge_<suite>.py`；
3. judge 输出 JSON 列表：`[{"name": "test_brk", "all": 3, "pass": 3, "score": 3}, ...]`；
4. `name` 是测试点名、`all` 是该点满分、`pass` 是通过的子项数、`score` 是得分；
5. **没跑到的测试点也必须在 JSON 里出现，得 0 分**——所以“OS 没执行这个脚本”
   和“执行了但失败”在分数上都是 0，区别只在报告里：
   前者进「未运行 / 未完成」，后者进「失分优先级」。

`report.md` 里每个测试点的 `note` 形如 `3/5`，就是 `pass/all`。

---

## 怎么用这些文档

- 报告里看到 `basic-glibc / test_mount 0/5` → 打开 [basic/basic.md](basic/basic.md)
  和 [basic/oscomp_syscalls.md](basic/oscomp_syscalls.md)，查 `mount` 那个点要求什么；
- 看到 `busybox which ls 0/1` → [busybox/busybox_cmd.txt](busybox/busybox_cmd.txt)
  里有完整的命令清单；
- 看到 `lmbench lat_syscall null` 超出预期区间 → [lmbench.md](lmbench.md) 查那行的区间；
- 想知道 `composite` 和官方分的差异从哪来 → 主 [README 第 3 章](../README.md)。
