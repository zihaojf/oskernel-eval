# StarryOS 本地评分器（starry-eval）

一个**完全独立于 StarryOS 仓库**的本地评测工具：用 QEMU 启动 `kernel-rv` /
`kernel-la` 和官方 `sdcard-*.img`，捕获串口输出，按赛题评分依据计算
「通过测试点数 / 分数 / 性能指标 / 失分优先级」，并给出优化建议。

> 设计约束：本目录放在 `~/starry-work/starry-eval`，是 StarryOS 的**兄弟目录**。
> 在线评测只执行 StarryOS 仓库里的 `make all` 并启动内核，不会包含本工具；
> 本工具也不会修改 StarryOS 的源码或 `init.sh`。

---

## 1. 它复现了什么

大赛评测的基本流程是：

1. QEMU 启动你的内核，挂载官方 EXT4 测试镜像；
2. 你的 OS 主动扫描镜像里的 `xxxxx_testcode.sh` 并串行执行；
3. 每个测试点用 `#### OS COMP TEST GROUP START xxxxx ####` /
   `#### OS COMP TEST GROUP END xxxxx ####` 包住输出；
4. 评测系统截取两行之间的文本，交给 `judge_xxxxx.py`，得到 JSON 测试点列表；
5. **没有运行的测试点不得分**；运行了但没打印 `END` 的测试点也拿不到完整分。

本工具复现第 1–4 步，并额外做三件事：

- 汇总每个 suite / libc 变体的 `score / max`、`full / partial / fail` 测试点数；
- 对没有官方 judge 的 suite（unixbench、iperf、libcbench、lmbench、netperf、
  cyclictest、ltp）按赛题文档实现本地评分/性能解析；
- 输出「失分优先级」和性能异常，直接指导下一轮优化。

---

## 2. 快速开始

环境要求：

- Python 3.10+（只用标准库）；
- `qemu-system-riscv64`、`qemu-system-loongarch64`（建议 QEMU 9/10）；
- StarryOS 仓库里已有 `kernel-rv`、`kernel-la`（`--build` 会先 `make rv` / `make la`）；
- 官方测试镜像 `sdcard-rv.img` / `sdcard-la.img`，放在 StarryOS 仓库根目录
  （4 GiB/个，不进 git，见第 13 节）；
- 官方 judge 脚本：**已内置在 `vendor/judge/`**，无需另外 clone 上游仓库；
  想用自己那份就 `--judge-dir` 覆盖（解析顺序：`--judge-dir` →
  `../oskernel-testsuits-cooperation/judge` → `vendor/judge`）。

```bash
cd ~/starry-work/starry-eval

# 查看 suite 清单和 judge 是否就绪
python3 starry_eval.py suites

# 只测 RISC-V / 只测 LoongArch / 两个都测
python3 starry_eval.py run --arch rv
python3 starry_eval.py run --arch la
python3 starry_eval.py run --arch all

# 重新编译再测（在 Docker/有工具链的环境中使用）
python3 starry_eval.py run --arch rv --build

# 评分一份已经抓好的串口日志（不启动 QEMU）
python3 starry_eval.py score rv.log --arch rv

# 对比两次报告
python3 starry_eval.py compare runs/old/report.json runs/new/report.json

# 与在线评测结果对齐（需要 rv + la 两份报告）
python3 starry_eval.py compare-online

# 把所有测试点摊平成一张表（report.md 只给 top 20 失分点）
python3 scripts/points_table.py runs/*/report.json
python3 scripts/points_table.py runs/*/report.json --only lost --out-dir /tmp/tables

# 或者用 Makefile 快捷方式
make test
make suites
make rv
make la
make all
make score LOG=rv.log
make points
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--timeout 2400` | QEMU 墙钟超时，默认 2400 s |
| `--no-snapshot` | 写回磁盘镜像（默认 `-snapshot`，每次从干净镜像启动） |
| `--build` | 先执行 `make rv` / `make la` |
| `--starry-dir` | StarryOS 路径，默认 `../StarryOS` |
| `--judge-dir` | `judge_*.py` 目录 |
| `--runs-dir` | 报告输出根目录，默认 `runs/` |
| `--baseline` | 性能基线 JSON |
| `--score-mode` | `both`（默认）/ `raw` / `intended`，见第 3.3 节 |
| `--save-baseline` | 把本次指标写入基线 |
| `--format` | `text` / `md` / `json` / `none` |

每次运行生成：

```text
runs/<时间戳>-<arch>/
  serial.log    # 原始串口日志（评测系统的输入）
  report.txt    # 终端摘要（同屏输出）
  report.md     # 详细 Markdown
  report.json   # 完整结构化结果（含全部测试点，`make points` 从这里摊平）
```

`python3 scripts/points_table.py`（或 `make points`）会额外写出：

```text
runs/<时间戳>-<arch>/
  points.md     # 每个测试点一行 + 按测试组小计
  points.csv    # 同数据，便于 diff/表格软件
```

---

## 3. 评分模型（重点）

### 3.1 两套总分

报告里有两个总分，含义不同，**不要混用**：

| 名称 | 范围 | 用途 |
|---|---|---|
| **official** | basic、busybox、lua、libctest、iozone | 直接调用官方 `judge_*.py`，最接近在线评测 |
| **composite** | 除 ltp 外的 11 个 suite | 本地扩展指标；无官方 judge 的 suite 使用下面 3.2 的规则 |

> **composite 不是在线总分。** 在线结果是 4 个环境（glibc/musl × la/rv）的总和，
> 且套件集合/部分公式不同；用 `python3 starry_eval.py compare-online` 做对齐，
> 详见 6.3。

`ltp` 不进入 composite：官方镜像里 `ltp/testcases/bin` 有 2822 个可执行文件，
分母是动态的，合成一个总分没有意义；报告单独显示 pass/fail/TCONF。

### 3.2 各 suite 的计分规则

| suite | 计分方式 | 满分（每个 libc 变体） |
|---|---|---|
| basic | 官方 `judge_basic.py`，按断言计分 | 102 |
| busybox | 官方 `judge_busybox.py`，每条命令 1 分 | 55（含两个 marker echo） |
| lua | 官方 `judge_lua.py`，每个脚本 1 分 | 9 |
| libctest | 官方 `judge_libctest.py`，static+dynamic 每个 case 1 分 | 220 |
| iozone | 官方 `judge_iozone.py`，每个指标 0–2 分（1 分=达到基线） | 40 |
| unixbench | **覆盖率**：每个 benchmark 跑出结果得 1 分 | 27 |
| libcbench | **覆盖率**：每个 benchmark 跑出结果得 1 分 | 27 |
| iperf | 6 个 case，报告 `end: success` 各 1 分 | 6 |
| netperf | 5 个 case，报告 `end: success` 各 1 分 | 5 |
| cyclictest | 4 次运行，`end: success` 且 max latency ≤1000us 各 1 分；hackbench 未成功 kill 时 STRESS 作废 | 4 |
| lmbench | **区间质量**：24 个指标按文档预期范围归一化，每个 0–1 分 | 24 |
| ltp | 通过率：PASS=1，FAIL=0，TCONF 不计入分母 | 动态 |

说明：

- `unixbench` / `libcbench` 的“覆盖率分”只表示跑完了，不代表快慢；真正的性能
  在报告的 **Performance** 表里，用 `quality` / `delta` 和原始值比较。
- `lmbench` 的 `quality` 使用 `doc/lmbench.md` 的预期范围：落在最好端=1，
  最差端=0；比最差端更差=0。QEMU TCG 下这些绝对延迟会明显偏大，重点看
  **同一台机器上前后两次运行的 delta**。
- `iozone` 的 0–2 分公式来自官方 judge：`baseline` 得 1 分，越快越接近 2 分；
  低于 baseline 仍保底 1 分。所以 40 分是理论上限，20 分左右表示“大多数指标
  刚达到基线”。

### 3.3 `basic` 的 judge 兼容性

`judge_basic.py` 用 `sys.stdin.readlines()` 保留行尾 `\n`，但部分断言是
`assert_equal("...", data[0])` 这种精确比较，因此**逐字执行官方 judge** 时
很多本来正确的行会被判错。报告同时给出：

- **verbatim**：完全按官方脚本执行的分数（在线评测如果原样使用该脚本，就是这个）；
- **intended**：去掉行尾换行、并按 `instance.name` 修正
  `test_dup2`/`dup2_test` 映射后的分数（更能反映 OS 的真实能力）。

默认 `--score-mode both`：主分数用 intended，verbatim 作为 note；
`--score-mode raw` 则主分数用 verbatim，适合和在线结果对齐。

---

## 4. 读报告

以一次完整的 RISC-V 运行为例：

```text
Totals
official judge (verbatim): 665.3 / 852  (78.1%)
official judge (intended): 737.3 / 852  (86.5%)
local composite      : 861.3 / 1038  (83.0%)
test points          : 737 full, 0 partial, 69 fail, 0 skip
groups               : 20/20 complete
```

- **official**：5 个有官方 judge 的 suite 之和；`/852` 是 2 个 libc 变体的
  `(102+55+9+220+40)`。
- **composite**：再加 unixbench/iperf/libcbench/lmbench/netperf/cyclictest
  的本地分；`/1038` 是 `2 × (102+55+9+220+40+27+6+27+24+5+4)`。
- **test points**：`full` 表示该测试点满分，`partial` 表示只拿到部分断言，
  `fail` 表示 0 分；未运行的 suite 不计入这个计数，而是在下一节单独列出。
- **groups**：串口里实际出现且完整闭合的 group 数。

### 4.1 Suite scores

```text
suite                var          score   points f/p/f     time  status       delta
basic                glibc     92/102     30/  0/2       2.4s  ok
...
libctest             glibc    172/220    172/  0/48     54.0s  ok
```

- `score/max` 是计入总分的值；未完成 group 的 `raw` 分会以
  `note: ... partial raw score ... (not counted)` 单独显示。
- `delta` 是相对**上一次完整运行**的同 suite 变化；没有可比的完整历史时为空。
- `basic` 的 verbatim/intended 差异会以 `note:` 行列出。

### 4.2 Not run / incomplete

列出没有 `START/END`、脚本运行了但没打印 group、或中途被 kill 的测试点。
**这些在在线评测里就是 0 分**，所以这是最需要关注的部分之一。

### 4.3 Lost points by suite

按 suite 汇总失分，直接告诉你“先修哪里收益最大”。例如 RISC-V 的结果：

```text
libctest-glibc           lost    48.0 across 48 test point(s)
iozone-musl              lost    19.9 across 20 test point(s)
iozone-glibc             lost    19.8 across 20 test point(s)
basic-glibc              lost    10.0 across 2 test point(s)
basic-musl               lost    10.0 across 2 test point(s)
libctest-musl            lost     3.0 across 3 test point(s)
unixbench-glibc          lost     3.0 across 1 test point(s)
...
```

### 4.4 Warnings

- `iperf ... UDP loss 97%`：UDP 发送端有数据，接收端只收到 3%，说明网络栈/
  缓冲区有问题（但 iperf 退出码仍是 0，所以 case 分仍拿满）。
- `iperf ... suspicious TCP retransmit counter 774909488`：TCP 重传计数异常，
  几乎可以断定是计数器/协议栈 bug。
- `cyclictest ... max latency 10140 us`：QEMU 下 100 Hz tick 的典型值；
  文档样例是 1–50 us。`STRESS ... discarded` 表示 hackbench 没能正常 kill。
- `netperf setsockopt(SO_DONTROUTE/SO_ENOBUFS) is not supported`：测试仍能跑，
  但 setup 降级；`netperf UDP_STREAM receiver ...% of sender` 表示大量丢包。
- `unixbench/missing-benchmarks`：通常是 `looper ./multi.sh` 相关命令失败。

### 4.5 Performance

每个 suite 一行一个指标，按“最差优先”排序：

```text
[iperf-glibc] 18 metrics
  iperf/BASIC_TCP tcp_retrans   774909488 count   quality=0.00 expected 0-1000000
  iperf/BASIC_UDP udp_loss      97 %              quality=0.03 expected 0-100 %
  ...
[libcbench-glibc] 27 metrics
  libcbench/b_pthread_create_serial1(0)  1.094 s  quality=1.00 delta=+0.0%
```

`quality`：

- 有文档区间（lmbench、iperf 丢包/重传、cyclictest 延迟）→ 区间质量；
- 没有文档区间（libcbench、unixbench、网络带宽）→ 相对 baseline 的比值，
  `1.0` 表示和 baseline 相同；
- 没有 baseline 时为空，只看原始值和先后对比。

---

## 5. 基线与历史对比

```bash
# 第一次：建立基线（建议在“当前最好的一次完整运行”后执行）
python3 starry_eval.py run --arch rv --save-baseline

# 之后：默认会读取 baseline.json；没有 scoped 指标时退回上一份完整报告
python3 starry_eval.py run --arch rv

# 明确指定基线，或禁用上一份报告作为基线
python3 starry_eval.py run --arch rv --baseline my-baseline.json
python3 starry_eval.py run --arch rv --no-previous-baseline

# 查看基线内容
python3 starry_eval.py baseline
```

`baseline.json` 的 key 是 `<arch>/<variant>/<metric>`，例如
`rv/glibc/libcbench/b_malloc_sparse(0)`。这样 RISC-V 和 LoongArch、
glibc 和 musl 不会互相污染。

**注意**：QEMU TCG 的绝对值受宿主机负载影响很大；优化迭代时请用同一台机器、
尽量在空闲时运行，并重点看 `delta` 而不是绝对分数。需要更接近真实性能时，
在有 KVM 的 Linux 主机或真实硬件上跑同样的判分流程。

---

## 6. 当前结果（本机实测，QEMU TCG）

> 时间：2026-10-31；StarryOS 当前提交；`sdcard-rv.img` / `sdcard-la.img`；
> `-smp 1 -m 1G`，无 KVM。绝对值只用于本机前后对比。
>
> 本次生成的两份报告：
> `runs/20261031-232838-rv-score/report.md`（RISC-V，score 自串口日志）和
> `runs/20261031-232847-la/report.md`（LoongArch，实时运行）。

| 架构 | official verbatim | official intended | composite | 完整 group | 实际耗时 |
|---|---:|---:|---:|---:|---:|
| RISC-V 64 | 665.3 / 852 | 737.3 / 852 | 861.3 / 1038 | 20 / 20 | ≈ 21 min |
| LoongArch64 | 627 / 852 | 698 / 852 | 774 / 1038 | 16 / 16 | ≈ 2.5 min |

> LoongArch 的 `init.sh` 主动跳过 iozone 和 unixbench（timer 不完整 / iozone
> 会挂），所以少了 `40.3 + 48 ≈ 88` 的 composite 分；功能套件与 RISC-V 基本持平。

### 6.1 已运行套件

| suite | RISC-V glibc | RISC-V musl | LA glibc | LA musl | 说明 |
|---|---:|---:|---:|---:|---|
| basic | 92/102 | 92/102 | 92/102 | 91/102 | mount/umount 各 0/5；LA musl 本次单点 flake |
| busybox | 53/55 | 53/55 | 53/55 | 53/55 | `which ls`、`kill 10` 失败 |
| lua | 9/9 | 9/9 | 9/9 | 9/9 | 全过 |
| libctest | 172/220 | 217/220 | 174/220 | 217/220 | glibc 是最大失分点 |
| iozone | 20.2/40 | 20.1/40 | 未运行 | 未运行 | LA 主动跳过 |
| unixbench | 24/27 | 24/27 | 未运行 | 未运行 | 缺 SHELL1/8/16 |
| iperf | 6/6 | 6/6 | 6/6 | 6/6 | 但 UDP 丢包 82–98% |
| libcbench | 27/27 | 27/27 | 27/27 | 27/27 | 覆盖率满 |
| netperf | 5/5 | 5/5 | 5/5 | 5/5 | 但 UDP 接收端严重丢包 |
| cyclictest | 0/4 | 0/4 | 0/4 | 0/4 | 延迟约 10ms，全部 0 分；STRESS 作废 |
| lmbench | 未运行 | 未运行 | 未运行 | 未运行 | `init.sh` 跳过 |
| ltp | 未运行 | 未运行 | 未运行 | 未运行 | `init.sh` 跳过 |

### 6.2 优化优先级（按当前报告）

1. **libctest glibc（失分 48）**：最大单点收益。失败集中在
   `clocale_mbfuncs`、`crypt`、`fnmatch`、`fscanf/fwscanf`、`mbc`、
   `pthread_cancel*`、`sscanf`、`strftime`、`strtol`、`swprintf`、`wcstol`、
   `regex_*`、`setvbuf_unget`、`sem_init`、`dn_expand_*` 等；static 和
   dynamic 基本同源。先按报告里的失败名逐个定位，优先修 syscall/线程/信号/
   locale 相关的一类问题。
2. **basic mount/umount（失分 20）**：两个 libc × 两个测试点 × 5 分。
   当前 `mount return: -19`（ENODEV），测试期望 `/dev/vda2`；官方镜像是
   无分区表的 EXT4，设备名/挂载语义要对齐。即使不能真正 mount，也要让
   `test_mount`/`test_umount` 走完并打印期望行。
3. **iozone（RISC-V 失分约 39.7）**：初始写约 980 kB/s，官方基线约 3524 kB/s；
   读/重写也低于基线。重点看 page cache/writeback、4 进程并发、fsync。
4. **unixbench（RISC-V 失分 6）**：缺 `SHELL1/8/16`，通常是
   `looper ./multi.sh` 依赖的 shell/工具或进程创建路径失败；补上即可拿满
   coverage。
5. **busybox（失分 4）**：`which ls`、`kill 10`。检查 PATH 查找和 kill 的
   错误返回语义。
6. **cyclictest（RISC-V 失分 8，LA 失分 8）**：RISC-V 上 NO_STRESS 能跑，但
   `sleep: not found`（脚本里的 `sleep 1` 失败）、hackbench fork 失败、
   STRESS 作废；tick 只有 100 Hz、无高精度定时器。LA 的 musl 版本 4 次全
   fail，串口直接打印 `unable to get scheduler parameters`，优先检查
   `sched_getparam` / `sched_getscheduler` / `sched_getaffinity` 等调度参数
   syscall 的 musl 兼容实现；同时给 `/bin/sleep`（busybox applet）补一个入口。
7. **iperf/netperf**：case 分已经拿满，但 UDP 97% 丢包、TCP 重传计数异常、
   netperf UDP 接收端只有发送端的 2%。这些是网络栈/缓冲区/协议正确性问题，
   当前不一定直接扣分，但很可能影响隐藏性能评分和稳定性。
8. **lmbench/ltp（尚未运行）**：当前 `init.sh` 主动跳过。lmbench 在 TCG 下
   极慢（`Simple write` 可能卡住），需要增大脚本 timeout 或减少 repetitions；
   ltp 有 2822 个 case，必须评估总时长后再决定是否启用。启用它们是 OS 侧
   决策，本评分器不会代改 `init.sh`。

### 6.3 与在线结果对齐（为什么本地 composite 和在线 1255 不同）

在线结果是 **4 个环境**（glibc-la / glibc-rv / musl-la / musl-rv）的总分
1255.15；本地 `composite` 是**单 arch** 的数字，而且套件集合/公式不同：

- 本地 composite 包含 **unixbench**（RISC-V 24+24=48），在线表没有这一行；
- 本地统计 **libctest-glibc**（172/174），在线表是 "-"；
- 本地 libcbench 是覆盖率 27、netperf 是 success 5、cyclictest 是 success 2，
  在线分别用 performance/validity 公式（27–32.2、5.48–5.71、0）。

用下面的命令对齐（默认读取 `runs/` 里各 arch 最近一次完整报告）：

```bash
python3 starry_eval.py compare-online
```

当前对齐结果：

| environment | local aligned | online | delta |
|---|---:|---:|---:|
| glibc-la | 192.00 | 197.86 | -5.86 |
| glibc-rv | 212.22 | 215.47 | -3.25 |
| musl-la | 408.00 | 412.14 | -4.14 |
| musl-rv | 429.11 | 429.67 | -0.57 |
| **TOTAL** | **1241.33** | **1255.15** | **-13.82** |

`local aligned` 只统计在线表里非 "-" 的 suite；unixbench（本地 48）和
libctest-glibc（本地 172/174）不计入。逐项差异：

| 来源 | 本地 | 在线 | 四环境合计差 |
|---|---:|---:|---:|
| libcbench 覆盖率 vs 性能公式 | 27 | 27.0–32.2 | -9.86 |
| netperf success vs 性能公式 | 5 | 5.48–5.71 | -2.52 |
| basic（LA musl 单点 flake） | 91 | 92 | -1.00 |
| iozone（run-to-run/基线） | 20.1–20.2 | 20.0–20.76 | -0.43 |
| cyclictest（已改为延迟阈值） | 0 | 0 | 0 |
| unixbench | RV 48 | 不在表中 | 不计入 aligned |
| libctest-glibc | 172/174 | "-" | 不计入 aligned |

**结论**：对齐套件集合后，本地与在线只差约 **13.8 分（~1.1%）**，不是
“差 400 分”。剩余差异几乎全部来自 libcbench/netperf 的在线性能基线公式；
如果拿到在线 `judge_libcbench.py` / `judge_netperf.py` 或它们使用的基线，
可以再精确复现。

**关于 libctest-glibc "-"**：本地 glibc libctest 耗时 54–60s，在线可能是被
per-test timeout 杀掉（没有 `END` 所以记 "-"），也可能在线只统计 musl。
建议先在在线串口里确认 `libctest-glibc` 是否有 `START/END`；如果是超时，
优先优化 glibc libctest 的耗时/稳定性。

---

## 7. 目录结构

```text
starry-eval/
├── starry_eval.py              # CLI 入口
├── Makefile                    # make rv / la / score / points 快捷方式
├── .gitignore                  # runs/、__pycache__ 不入库
├── README.md
├── baseline.json               # 性能基线（可删除后重建）
├── online_2026.json            # 在线评测结果快照（仅 compare-online 需要）
├── scripts/
│   └── points_table.py         # 把 report.json 摊平成全量测试点表（md + csv）
├── vendor/
│   └── judge/                  # 官方 judge_*.py 原件副本（84 KB，见 NOTICE.md）
├── runs/                       # 每次运行的 serial.log + report.*（未入库）
├── tests/
│   ├── test_parsers.py         # 解析器的合成数据单测
│   └── test_paths.py           # judge 解析顺序 / 内核镜像路径的单测
└── starry_eval/
    ├── cli.py                  # 参数解析、子命令
    ├── runner.py               # build → QEMU → 评分 → 落盘
    ├── qemu.py                 # QEMU 进程、串口捕获、超时/杀进程组
    ├── serial_parser.py        # START/END、running/finished 解析
    ├── evaluate.py             # group → suite 聚合、未运行/未完成处理
    ├── report.py               # text / md / json 报告
    ├── history.py              # runs 索引、baseline、上一份完整报告
    ├── compare.py              # 两份 report.json 的差异
    ├── online.py               # 与在线结果按环境/套件对齐
    ├── config.py               # 路径、架构、QEMU 命令、超时
    ├── references.py           # suite 目录、文档预期范围、case 清单
    ├── model.py                # TestPoint / Metric / GroupResult / ArchResult
    └── scoring/
        ├── official.py         # 官方 judge 逐字执行 + basic intended 模式
        ├── libcbench.py        # 覆盖率 + 时间指标
        ├── unixbench.py        # 覆盖率 + 指标
        ├── network.py          # iperf / netperf
        ├── cyclictest.py       # 实时延迟 + hackbench 作废规则
        ├── lmbench.py          # 24 指标 + 区间质量
        ├── ltp.py              # PASS/FAIL/TCONF
        ├── iozone.py           # 官方 judge 不可用时的 fallback
        └── common.py           # 数值/单位/分段/指标工具
```

---

## 8. 扩展方式

### 新增一个 suite

1. 在 `references.py` 的 `SUITE_ORDER` / `SUITE_SPECS` 中登记；
2. 在 `scoring/` 里实现 `score_xxx(group, baseline)`；
3. 在 `scoring/__init__.py` 的 `LOCAL_PARSERS` 中注册；
4. 如果有官方 judge，把 `judge_xxx.py` 放到 `--judge-dir`，并把 suite 加进
   `OFFICIAL_SUITES`；
5. 在 `tests/test_parsers.py` 加一个合成输出用例。

### 调整评分权重/预期范围

- lmbench 的 24 个区间在 `references.py:LMBENCH_SPECS`；
- UDP 丢包、TCP 重传、cyclictest 延迟阈值在 `references.py` 顶部；
- 各 suite 的默认满分在 `SUITE_SPECS.default_max`。

---

## 9. 与在线评测的隔离

- 本工具的所有文件都在 `~/starry-work/starry-eval`，不在 StarryOS 的 git
  工作树里；`make all` 不会引用它。
- 本工具只**读** StarryOS 的 `kernel-*` / `sdcard-*.img`；`--build` 会触发
  正常的 `make`，写 `target/`、`.cargo/`、`kernel-*`，与手工编译一致。
- 本工具**不修改** `src/init.sh`、内核源码或磁盘镜像；默认 `-snapshot`
  保证每次从干净镜像启动。
- 不要把 `starry-eval/` 复制进 StarryOS 仓库再提交；在线评测的构建只需要
  `Makefile`、`src/`、`kernel/`、`vendor/` 等原有内容。
- 磁盘镜像和 `kernel-*` 本来就在 `.gitignore` 中；本工具也不会提交任何东西。

---

## 10. 已知限制

1. **QEMU TCG 不是真实硬件**：延迟/带宽绝对值偏大，且受宿主负载影响；
   请用同一台机器、同一时段的运行做前后对比。
2. **官方 judge 版本可能不同**：本工具直接执行
   `../oskernel-testsuits-cooperation/judge/judge_*.py`；如果在线 judge 修复了
   `basic` 的换行问题，请用 `--score-mode intended` 或对照两种总分。
3. **线上最大分可能不同**：本工具按当前文档给出官方 5 套件和本地 composite；
   如果线上只评部分 suite 或有不同权重，以在线为准，本报告用于定位和优化。
4. **未运行的 suite 不会自动补跑**：OS 的 `init.sh` 决定跑哪些测试点；
   本工具不修改它。想跑 lmbench/ltp，需要单独调整 OS 侧脚本。
5. **`ltp` 分母动态**：官方镜像有 2822 个 case；本工具只做通过率统计，
   不把它放进 composite。
6. **性能指标没有绝对“及格线”**：没有文档范围的 suite 使用上一次完整运行
   作为 baseline；第一次运行时只能看原始值。
7. **在线 libcbench/netperf 使用未公开的性能基线/公式**：本地当前分别用
   覆盖率（27）和 success（5）计分，因此会比在线低约 10 / 2.5 分（四环境
   合计）。`compare-online` 会把这两个差异单独列出来；如果拿到在线的
   `judge_libcbench.py` / `judge_netperf.py` 或基线，可以再精确复现。

---

## 11. 单测

```bash
cd ~/starry-work/starry-eval
python3 -m unittest discover -s tests -v
```

当前覆盖：lmbench 24 指标（含无标签的 `lat_mmap`/`bw_*`/`lat_fs`/`lat_ctx`）、
ltp PASS/FAIL/TCONF、netperf 5 case、iperf 6 case、cyclictest STRESS 作废、
libcbench/unixbench 覆盖率、串口 group/incomplete/script marker。

---

## 12. 独立成库 / 迁移到其他机器

本目录**可以单独作为一个 GitHub 仓库**（仓库根 = `starry-eval/`），
但仓库里只应包含工具本体；内核、镜像、judge、运行产物都不进去。

### 12.1 入库内容

| 入库 | 说明 |
|---|---|
| `starry_eval.py`、`starry_eval/` | 工具本体，纯标准库 |
| `scripts/points_table.py` | 全量测试点表导出 |
| `vendor/judge/` | 官方 `judge_*.py` 原件副本，clone 即可评分（来源与 commit 见 `vendor/judge/NOTICE.md`） |
| `tests/`、`Makefile`、`.gitignore` | 单测 / 快捷入口 / 忽略规则 |
| `README.md` | 本文件 |
| `baseline.json` | 可选，见 12.3 |
| `online_2026.json` | 可选，仅 `compare-online` 需要，见 12.3 |

不入库：

| 排除 | 原因 |
|---|---|
| `runs/` | 含 `serial.log` + 报告，且报告里嵌了本机绝对路径；已在 `.gitignore` |
| `StarryOS/` | 内核与 `sdcard-*.img`（GB 级），属另一个仓库 |
| `oskernel-testsuits-cooperation/` | 上游文档仓库；judge 已单独 vendor 进 `vendor/judge/`，没必要整仓搬运 |
| `__pycache__/`、`*.pyc`、`*.out` | 已在 `.gitignore` |

### 12.2 换机器后的路径

judge 已内置（`vendor/judge/`），所以评分只差一份日志：

```bash
# 只评已有日志：不碰内核、不碰镜像、不用给 --judge-dir
python3 starry_eval.py score rv.log --arch rv

# 起 QEMU：只需要指出 StarryOS 在哪
python3 starry_eval.py run --arch rv --starry-dir /path/to/StarryOS
```

`kernel-*` / `sdcard-*.img` 的查找顺序（见第 14 章）：

| 产物 | 第一候选 | 兜底 |
|---|---|---|
| 内核 | `<starry-dir>/kernel-rv` | `<repo>/kernels/kernel-rv` |
| 镜像 | `<starry-dir>/sdcard-rv.img` | `<repo>/images/sdcard-rv.img` |

`<starry-dir>` 自动识别 `../StarryOS`、`../Oops-StarryOS`、`<repo>/StarryOS`
（submodule）三种位置，都不存在且没给 `--starry-dir` 时按 `../StarryOS` 报错。

三条 judge 兜底路径（`--judge-dir` 永远最先）：`--judge-dir` →
`../oskernel-testsuits-cooperation/judge`（改 judge 时的上游 checkout）→
`vendor/judge/`（本仓库自带）。三条都不存在才会报错；此时加
`--allow-missing-judges` 只跑本地 parser，但 `basic / busybox / lua /
libctest` 没有本地兜底，会记 0 分——那不是内核的问题，别拿它做结论。

### 12.3 `baseline.json` 与 `online_2026.json` 的取舍

- `baseline.json` 是**本机 QEMU 测出来的**性能基线（iozone/cyclictest/lmbench
  等）。上传无害，但别人机器上的 `delta` 会失真；要么一起传并在 README 注明
  来源机器，要么不传，让别人第一次 `run --save-baseline` 自建。
- `online_2026.json` 是**你自己那次在线评测的成绩快照**。传上去等于公开成绩
  数字；只想给工具的话可以删掉，代价是 `compare-online` 不可用（其它子命令
  不受影响）。

### 12.4 上传前自检

```bash
cd starry-eval
python3 -m unittest discover -s tests        # 8 个用例应全过
make suites                                  # judge 就绪时 official judge 列应为 yes
git init && git add -A && git status --short # 确认没有 runs/、__pycache__、*.img
git ls-files -s | wc -l                      # 约 32 个文件、~310 KB
grep -rn "/home/\|/Users/" --include='*.py' --include='*.json' --include='*.md' . \
    | grep -v runs/                          # 应无输出（代码与数据都不含本机路径）
```

---

## 13. 分发给队友（对方只有 StarryOS）

队友手上只有 StarryOS 源码时，缺的是**三样**：judge 脚本、测试镜像、本工具。

| 内容 | 体积 | 分发方式 |
|---|---|---|
| 本仓库（工具 + `vendor/judge/`） | ~400 KB | `git clone` |
| `sdcard-rv.img` / `sdcard-la.img` | 4 GiB / 个 | GitHub **Release 附件**（仓库里放不下：单文件 100 MB 硬限） |
| `kernel-rv` / `kernel-la` | 4–5 MB | 队友自己 `make rv` / `make la`，不用你给 |

### 13.1 镜像压缩与上传

xz 对 ext4 镜像压得很狠（实测 `xz -1 -T8`）：

```bash
cd ~/starry-work/StarryOS
sha256sum sdcard-rv.img sdcard-la.img
# 259d16ab9f5a909f4a8d454d5704b1c3ee62224884f4714d06ce7bd0948bb831  sdcard-rv.img
# 1409a027fad80220c8e114fdfcff316c16f9dcd7772f1aff471d3f99d480dff6  sdcard-la.img

xz -1 -T0 -k sdcard-rv.img     # 4.0 GiB -> 520 MiB (7.9x)，约 60-90 s
xz -1 -T0 -k sdcard-la.img     # 4.0 GiB -> 708 MiB (5.8x)，约 90-120 s
```

`xz -9` 能再小一点，但时间翻几倍；Release 单文件上限 2 GiB，`-1` 已经
足够。上传（需要 GitHub CLI 已登录）：

```bash
gh release create images-v1 \
    --title "official sdcard images (rv/la)" \
    --notes "xz -1 压缩；解压后 sha256 见 13.3" \
    sdcard-rv.img.xz sdcard-la.img.xz
```

不要塞进 git 本体：单文件 100 MB 硬限；LFS 免费额度只有 1 GB，两份镜像
解压前就 1.1 GB，Release 附件更省事。校内网盘/对象存储同样可以，把链接
写进 13.3 的表格即可。

### 13.2 队友侧的四步

```bash
# 1) 拿工具（judge 已内置，不用再 clone oskernel-testsuits-cooperation）
git clone <your-repo> starry-eval

# 2) 装 QEMU（Ubuntu 示例）
sudo apt install -y qemu-system-misc

# 3) 解压镜像到 StarryOS 根目录，名字必须是 sdcard-rv.img / sdcard-la.img
cd StarryOS
xz -dk ~/Downloads/sdcard-rv.img.xz
xz -dk ~/Downloads/sdcard-la.img.xz
sha256sum sdcard-*.img        # 与 13.3 的表对照

# 4) 编内核 + 起 QEMU + 出报告
cd ../starry-eval
make rv                        # 等价于 run --arch rv；镜像缺了就 --build
python3 starry_eval.py run --arch la
make points                    # 全量测试点表
```

`starry-eval/` 与 `StarryOS/` 必须是**兄弟目录**（默认路径推导就是这个
关系）。队友放在别处就加 `--starry-dir /path/to/StarryOS`。

### 13.3 分发清单（贴给队友）

| 项 | 值 |
|---|---|
| 工具仓库 | `<your-repo>`（约 400 KB，含 `vendor/judge/`） |
| `sdcard-rv.img` | Release 附件 `sdcard-rv.img.xz`（520 MiB），解压后 sha256 `259d16ab9f5a909f4a8d454d5704b1c3ee62224884f4714d06ce7bd0948bb831` |
| `sdcard-la.img` | Release 附件 `sdcard-la.img.xz`（708 MiB），解压后 sha256 `1409a027fad80220c8e114fdfcff316c16f9dcd7772f1aff471d3f99d480dff6` |
| Python | 3.10+（只用标准库） |
| QEMU | `qemu-system-riscv64`、`qemu-system-loongarch64` |
| 首次完整跑 | rv ≈ 6–9 分钟、la ≈ 3 分钟（QEMU TCG，视宿主而定） |

### 13.4 已验证的“队友环境”

把 `StarryOS/` 里只留 `kernel-rv` + `sdcard-rv.img`（无 judge、无上游仓库），
再放一份仅含本仓库的 clone 实测：

```text
$ python3 starry_eval.py run --arch rv --timeout 90
judge dir : .../starry-eval/vendor/judge        # 自动回退到内置 judge
official judge (verbatim): 354 / 852  (41.5%)   # 90 s 超时，只跑完 9/10 组
local composite          : 480 / 1038 (46.2%)
test points: 494 full, 1 partial, 140 fail, 0 skip
groups: 9/10 complete                            # timed_out: True
```

超时被杀也会正常写 `report.json` / `report.md` / `report.txt`，不会白跑。
默认 `--timeout 2400`（40 分钟）足够两个架构跑完。

---

## 14. 两仓库协作（Oops-StarryOS + oskernel-eval）

推荐布局：**OS 一个仓库、评分器一个仓库、镜像走 Release**。两者之间不加
submodule，靠目录约定 + 内置内核副本解耦。

```text
team/
├── Oops-StarryOS/      # 你的 OS 仓库（origin 仍指向赛题 GitLab）
│   ├── src/  kernel/  Makefile
│   ├── kernel-rv  kernel-la        # 构建产物，.gitignore 已排除
│   └── sdcard-rv.img  sdcard-la.img
└── oskernel-eval/      # 评分器仓库（队友 clone 这个）
    ├── vendor/judge/               # 官方 judge，内置
    ├── kernels/                    # 可选：预编译内核，见 14.3
    ├── images/                     # 可选：镜像放这里也行，见 14.2
    └── scripts/points_table.py
```

### 14.1 别把 OS 仓库的 origin 换掉

你的 `Oops-StarryOS` 工作副本当前是：

```text
origin    https://gitlab.eduxiji.net/T2026104869912739/oskernel2026-oops   # 赛题提交仓库
upstream  https://github.com/Starry-OS/StarryOS.git                       # 上游
```

**保持 origin 不动**，另加一个 remote 推到 GitHub：

```bash
cd ~/starry-work/StarryOS
git remote add github https://github.com/zihaojf/Oops-StarryOS.git
git push github main
```

把 `origin` 改成 GitHub 会直接断掉赛题提交流程，这是这套双仓库方案里唯一
不能碰的东西。

推送体积（实测）：已跟踪文件 11 667 个 / **155 MB**，最大的单文件 2.0 MB
（`vendor/x86-0.52.0/x86data/.../cascadelakex_core_v1.04.json`），没有超过
GitHub 100 MB 单文件限制的文件；`target/`（1.6 GB）、`*.img`、`kernel-rv`、
`kernel-la`、`.cargo` 都已在 `.gitignore` 里。首次 `push` 会带上历史
（`.git` 1.4 GB），之后都是增量。只想给队友一份代码、不要历史：

```bash
git checkout --orphan snapshot
git add -A && git commit -m "Oops-StarryOS snapshot"
git push github snapshot:main
```

### 14.2 队友要的东西：镜像放哪都行

镜像有两条合法位置，`starry-eval` 会自己找：

| 位置 | 适用 |
|---|---|
| `Oops-StarryOS/sdcard-rv.img` | 自己编内核的队友（推荐，和内核同目录） |
| `oskernel-eval/images/sdcard-rv.img` | 只想跑分、不想要 OS 源码的人 |

查找顺序固定：`--starry-dir` 指过去的那份 → `oskernel-eval/images/`。
内核同理：`<starry-dir>/kernel-rv` → `oskernel-eval/kernels/kernel-rv`。

### 14.3 想让“pull 评分器就拿到最新 OS 结果”，把内核提交进去

`kernel-rv` 4.8 MB、`kernel-la` 3.9 MB，完全可以进 git。做法：

```bash
cd ~/starry-work/StarryOS && make rv la          # 编出最新内核
cd ~/starry-work/starry-eval
mkdir -p kernels && cp ../StarryOS/kernel-rv ../StarryOS/kernel-la kernels/
git add -f kernels/                              # 强制加入（无 .gitignore 阻挡）
git commit -m "kernels: rv/la @ $(git -C ../StarryOS rev-parse --short HEAD)"
```

这样队友 `git pull` 评分器仓库 + 一份镜像就能直接 `make rv` 起 QEMU，
**不需要 Rust 工具链、不需要 OS 源码**。代价是内核是二进制、看不出改动
来源；commit message 里带上 OS 的 commit id 就能对上。

注意：预编译内核对工具链版本敏感（`rust-toolchain.toml` 里的
`nightly-2025-05-20`）。你构建机的 QEMU 版本也应和队友一致，否则
`cyclictest` / `lmbench` 这类指标不可比。

### 14.4 为什么不用 submodule

`git submodule add Oops-StarryOS StarryOS` 也能工作（`starry-eval` 会认
仓库内的 `starry_eval/StarryOS`），但两个代价：

1. 每次 OS 有改动，评分器仓库需要额外一次 commit 来 bump 指针，否则队友
   `git pull` 拿到的还是旧 commit；
2. 队友必须记得 `git clone --recursive` 或 `git submodule update --init`，
   忘了就是“目录空的”这种低级故障，队伍里 2–5 个人不值得付这个摩擦。

真要加也可以，命令如下，之后仍然建议用 `--starry-dir` 显式指路径：

```bash
cd ~/starry-work/starry-eval
git submodule add https://github.com/zihaojf/Oops-StarryOS.git StarryOS
git commit -m "StarryOS as submodule"
```

### 14.5 队友的第一天

```bash
git clone https://github.com/zihaojf/oskernel-eval.git
cd oskernel-eval
mkdir -p images && xz -dk ~/Downloads/sdcard-rv.img.xz -c > images/sdcard-rv.img
make suites            # 应显示 judge dir = .../vendor/judge
make rv                # 用 kernels/kernel-rv 或 ../Oops-StarryOS/kernel-rv
make points            # 全量测试点表
```

要改 OS 的人再 clone 一份 OS 仓库到**同级目录**，两个目录名都在识别范围
里（`StarryOS` / `Oops-StarryOS`），不需要任何参数。

> 仓库是 private 的话，队友需要被加成 collaborator，或用 PAT
> （`https://<token>@github.com/...`）才能 clone；Release 附件同理。
