#!/usr/bin/env bash
# Refresh kernels/ from an OS checkout and commit the result.
#
#   ./scripts/update-kernels.sh                 # uses ../Oops-StarryOS
#   ./scripts/update-kernels.sh /path/to/StarryOS
#   ./scripts/update-kernels.sh --no-push
#
# Teammates get the new kernel with a plain `git pull` of *this* repo, so they
# never need a Rust toolchain or the OS checkout just to score a run.
set -euo pipefail

push_after=1
os_dir=""
for arg in "$@"; do
    case "$arg" in
        --no-push) push_after=0 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) os_dir="$arg" ;;
    esac
done

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

if [ -z "$os_dir" ]; then
    for candidate in ../Oops-StarryOS ../StarryOS; do
        if [ -d "$candidate/src" ]; then os_dir="$candidate"; break; fi
    done
fi
if [ -z "$os_dir" ] || [ ! -d "$os_dir/src" ]; then
    echo "error: OS checkout not found (pass it explicitly: $0 /path/to/StarryOS)" >&2
    exit 2
fi

for kernel in kernel-rv kernel-la; do
    if [ ! -f "$os_dir/$kernel" ]; then
        echo "error: $os_dir/$kernel is missing; run 'make rv la' there first" >&2
        exit 2
    fi
done

rev=$(git -C "$os_dir" rev-parse --short HEAD 2>/dev/null || echo unknown)
name=$(basename "$os_dir")

mkdir -p kernels
cp -p "$os_dir/kernel-rv" "$os_dir/kernel-la" kernels/

echo "==> kernels/ updated from $name @ $rev"
sha256sum kernels/kernel-rv kernels/kernel-la

git add kernels/
if git diff --cached --quiet -- kernels/; then
    echo "==> kernels unchanged, nothing to commit"
    exit 0
fi

git commit -m "kernels: prebuilt rv/la @ $name $rev"

if [ "$push_after" = 1 ]; then
    git push
    echo "==> pushed; teammates get this kernel with 'git pull'"
else
    echo "==> committed locally (--no-push)"
fi
