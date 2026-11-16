#!/bin/sh
# Run Studio with the Python/Tk shipped next to the formal engines.
set -eu

prepare_runtime() (
    payload=$1
    target=$2
    suite=$target/oss-cad-suite
    export LC_ALL=C
    mkdir -p -- "$target"
    exec 9>"$target/.extract.lock"
    flock 9
    if [ -d "$suite" ]; then
        exit 0
    fi

    printf '%s\n' 'Preparing bundled OSS CAD Suite on first launch...' >&2
    staging=$(mktemp -d -- "$target/.unpack.XXXXXX")
    trap 'rm -rf -- "$staging"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    cd -- "$staging"
    cat -- "$payload"/oss-cad-suite.tgz.part-?? > oss-cad-suite.tgz || {
        printf '%s\n' 'Bundled OSS CAD Suite parts could not be read or joined. Check the ZIP and disk space.' >&2
        exit 2
    }
    if ! sha256sum --check --strict --status "$payload/SHA256SUMS"; then
        printf '%s\n' 'Bundled OSS CAD Suite checksum mismatch. Extract a fresh copy of the GitHub ZIP.' >&2
        exit 2
    fi
    tar -xzf oss-cad-suite.tgz --no-same-owner --exclude='*/__pycache__' || {
        printf '%s\n' 'Bundled OSS CAD Suite extraction failed. Check the disk space in the project directory.' >&2
        exit 2
    }
    mv -T -- oss-cad-suite "$suite"
)

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -s):$(uname -m)" in
    Linux:x86_64|Linux:amd64) arch=x64; loader=ld-linux-x86-64.so.2 ;;
    Linux:aarch64|Linux:arm64) arch=arm64; loader=ld-linux-aarch64.so.1 ;;
    *) exec python3 "$@" ;;
esac
root=$here
while [ "$root" != / ]; do
    target="$root/formal_runtime/linux-$arch"
    suite="$target/oss-cad-suite"
    if [ -d "$suite" ]; then
        break
    fi
    payload="$root/runtime_packages/linux-$arch"
    if [ -f "$payload/SHA256SUMS" ]; then
        prepare_runtime "$payload" "$target"
        break
    fi
    root=$(dirname -- "$root")
done
if [ "$root" = / ]; then
    exec python3 "$@"
fi
if [ ! -x "$suite/lib/$loader" ] || [ ! -x "$suite/libexec/python3.11" ]; then
    printf '%s\n' "Incomplete bundled Formal runtime: $suite" >&2
    exit 2
fi
# Set the bundled prefix at startup, then remove it from the environment before
# Studio starts any site-supplied tools or Make jobs.
export PYTHONHOME="$suite" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONEXECUTABLE="$here/run_python.sh"
unset PYTHONPATH
export test_mode_BUNDLED_PYTHON=1
exec "$suite/lib/$loader" --inhibit-cache --inhibit-rpath "" --library-path "$suite/lib" "$suite/libexec/python3.11" \
    -c 'import os, runpy, sys
os.environ.pop("PYTHONHOME", None)
os.environ.pop("PYTHONEXECUTABLE", None)
sys.argv = sys.argv[1:]
if sys.argv[0] == "-m":
    sys.argv = sys.argv[1:]
    runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
elif sys.argv[0] == "-c":
    code = sys.argv[1]
    sys.argv = ["-c"] + sys.argv[2:]
    exec(compile(code, "<string>", "exec"), {"__name__": "__main__"})
else:
    sys.path[0] = os.path.dirname(os.path.abspath(sys.argv[0]))
    runpy.run_path(sys.argv[0], run_name="__main__")' "$@"
