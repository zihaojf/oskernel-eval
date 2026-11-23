# Bundled official judge scripts

These files are a **verbatim copy** of the competition's automatic scoring
scripts, so that a fresh clone can score a run without a second checkout.
They are not part of this tool and are not modified here.

- Upstream: <https://github.com/oscomp/oskernel-testsuits-cooperation>
- Path: `judge/`
- Upstream commit: `44fa7ac65f292580c3974a8252f533c4484ebe6b` (master, 2025-04-07)

| file | role |
|---|---|
| `judge_basic.py` | `basic` suite, one point per assertion (has the trailing-newline quirk, see README section 3.3) |
| `judge_busybox.py` | `busybox` suite, one point per command |
| `judge_lua.py` | `lua` suite, one point per script |
| `judge_libctest.py` | `libctest` suite, static + dynamic, one point per case |
| `judge_iozone.py` | `iozone` suite, 0–2 points per metric against a fixed baseline |
| `lmbench.sh` | reference driver for the lmbench metrics |
| `README.md` | upstream description of the judge protocol (Chinese) |

Resolution order used by `starry_eval.config.default_paths`
(`--judge-dir` always wins):

1. `../oskernel-testsuits-cooperation/judge` (a full upstream checkout)
2. `vendor/judge/` (this directory)

To update, replace these files from the upstream repo and bump the commit id
above. If upstream fixes the `basic` newline handling, `--score-mode intended`
becomes the wrong lens — compare both modes before trusting a delta.
