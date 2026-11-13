#!/bin/sh
# Run Studio with the Python/Tk shipped next to the formal engines.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -m)" in
    x86_64|amd64) arch=x64; loader=ld-linux-x86-64.so.2 ;;
    aarch64|arm64) arch=arm64; loader=ld-linux-aarch64.so.1 ;;
    *) exec python3 "$@" ;;
esac
runtime=$here
while [ ! -d "$runtime/formal_runtime" ] && [ "$runtime" != / ]; do
    runtime=$(dirname -- "$runtime")
done
suite="$runtime/formal_runtime/linux-$arch/oss-cad-suite"
if [ ! -d "$suite" ]; then
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
export CMDBUF_BUNDLED_PYTHON=1
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
