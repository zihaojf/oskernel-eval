# Convenience entry points for the local scorer.
#
# These targets only call the Python CLI; they never touch the StarryOS tree.

.PHONY: help test suites rv la all score points

help:
	@echo "make test     run parser unit tests"
	@echo "make suites   list the suite catalogue and judge status"
	@echo "make rv       boot kernel-rv + sdcard-rv.img and score"
	@echo "make la       boot kernel-la + sdcard-la.img and score"
	@echo "make all      boot both architectures sequentially"
	@echo "make score LOG=path/to/serial.log  score a saved log"
	@echo "make points   flatten all test points of every stored report into one table"

test:
	python3 -m unittest discover -s tests -v

suites:
	python3 starry_eval.py suites

rv:
	python3 starry_eval.py run --arch rv

la:
	python3 starry_eval.py run --arch la

all:
	python3 starry_eval.py run --arch all

score:
	@test -n "$(LOG)" || (echo "usage: make score LOG=path/to/serial.log" >&2; exit 2)
	python3 starry_eval.py score "$(LOG)"

points:
	@ls runs/*/report.json >/dev/null 2>&1 || \
		(echo "no stored reports under runs/ yet; run 'make rv' / 'make la' / 'make score LOG=...' first" >&2; exit 2)
	python3 scripts/points_table.py runs/*/report.json
