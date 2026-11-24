# Convenience entry points for the local scorer.
#
# These targets only call the Python CLI; they never touch the StarryOS tree,
# except `make kernel` / the default build step, which runs the OS Makefile to
# refresh kernel-rv / kernel-la from source before booting them.

.PHONY: help test suites kernel rv la all score points

# Rebuild the kernel from source before booting it (BUILD=0 keeps the
# existing kernel-rv / kernel-la and boots them as-is).
BUILD ?= 1
BUILD_ARG = $(if $(filter 0,$(BUILD)),,--build)

help:
	@echo "make test     run parser unit tests"
	@echo "make suites   list the suite catalogue and judge status"
	@echo "make kernel   rebuild kernel-rv + kernel-la from the StarryOS tree only"
	@echo "make rv       rebuild kernel-rv, then boot + score (BUILD=0 to skip the build)"
	@echo "make la       rebuild kernel-la, then boot + score (BUILD=0 to skip the build)"
	@echo "make all      rebuild both kernels, then boot + score (BUILD=0 to skip the build)"
	@echo "make score LOG=path/to/serial.log  score a saved log"
	@echo "make points   flatten all test points of every stored report into one table"

test:
	python3 -m unittest discover -s tests -v

suites:
	python3 starry_eval.py suites

kernel:
	python3 starry_eval.py build --arch all

rv:
	python3 starry_eval.py run --arch rv $(BUILD_ARG)

la:
	python3 starry_eval.py run --arch la $(BUILD_ARG)

all:
	python3 starry_eval.py run --arch all $(BUILD_ARG)

score:
	@test -n "$(LOG)" || (echo "usage: make score LOG=path/to/serial.log" >&2; exit 2)
	python3 starry_eval.py score "$(LOG)"

points:
	@ls runs/*/report.json >/dev/null 2>&1 || \
		(echo "no stored reports under runs/ yet; run 'make rv' / 'make la' / 'make score LOG=...' first" >&2; exit 2)
	python3 scripts/points_table.py runs/*/report.json
