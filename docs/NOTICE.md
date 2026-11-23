# 来源与版权

`docs/` 下的文件（本 `README.md` 与 `NOTICE.md` 除外）是赛题协作文档仓库的
原文副本，**未做任何修改**，用于回答“这个测试点考什么、怎么算分”。

- Upstream: <https://github.com/oscomp/oskernel-testsuits-cooperation>
- Upstream commit: `44fa7ac65f292580c3974a8252f533c4484ebe6b` (master, 2025-04-07)
- 本地副本来自: `oskernel-testsuits-cooperation/doc/` 与 `oskernel-testsuits-cooperation/lmbench/`

## 文件对应关系

| 本目录 | 上游路径 |
|---|---|
| `Unixbench.md` | `doc/Unixbench.md` |
| `libctest.md` | `doc/libctest.md` |
| `lmbench.md` | `doc/lmbench.md` |
| `lmbench/lmbench.md` | `lmbench/lmbench.md` |
| `basic/` | `doc/basic/` |
| `busybox/` | `doc/busybox/` |
| `cyclictest/` | `doc/cyclictest/` |
| `iperf/` | `doc/iperf/` |
| `libcbench/` | `doc/libcbench/` |
| `ltp/` | `doc/ltp/` |
| `lua/` | `doc/lua/` |

`doc/lmbench.md` 与 `lmbench/lmbench.md` 是上游两份不同的文件（一份指标区间、
一份原理介绍），这里都保留了，路径也与上游一一对应。

## 维护

上游更新后按上表覆盖即可，并在本文件里更新 commit id。不要在本目录里改
内容——需要补充本工具自己的解释，写在 `docs/README.md` 或主 README 里。
