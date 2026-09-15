#!/bin/bash
#
# tools/build_mpy.sh - Precompiles core/ and drivers/ (and web/server.py,
# pins.py) to .mpy bytecode via mpy-cross, mirroring the source directory
# structure into build/.
#
# WHY: raw .py source costs more flash space and more RAM/time at import
# (MicroPython compiles it fresh every boot) than precompiled .mpy
# bytecode. For a project this size (~130KB of our own source before
# vendored libraries), precompiling is a real, standard optimization -
# not a guess.
#
# NOT PRECOMPILED, deliberately:
#   - /boot.py, /main.py: kept as plain .py. These are tiny bootstrap
#     entry points; MicroPython looks for them by exact name at boot,
#     and keeping them as readable source makes on-device debugging via
#     the REPL simpler without recompiling for a one-line change.
#   - web/static/*: not Python at all (HTML/CSS), copied as-is.
#   - config.json, module_registry.json: runtime-generated data files,
#     not part of the source tree.
#
# CRITICAL VERSION-MATCHING REQUIREMENT, not optional:
# mpy-cross output is tied to the exact MicroPython VERSION (and often
# the target architecture/feature set) it was built from. A .mpy file
# compiled with a mismatched mpy-cross will fail to load on the device
# firmware - the pinned version below is DEFAULT-ILLUSTRATIVE, not
# verified against whatever MicroPython build you actually flash. Check
# `import sys; sys.implementation` on your device (via the REPL) and use
# the matching mpy-cross release - see:
#   https://github.com/micropython/micropython/tree/master/mpy-cross
#
# This script does not install mpy-cross for you. It checks for the
# command and fails clearly with instructions if it's missing, rather
# than silently skipping compilation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$REPO_ROOT/build"

if ! command -v mpy-cross >/dev/null 2>&1; then
    echo "ERROR: mpy-cross not found on PATH." >&2
    echo "" >&2
    echo "mpy-cross must be built or downloaded to match the EXACT MicroPython" >&2
    echo "firmware version flashed on the device - a mismatched version will" >&2
    echo "produce .mpy files that fail to load. Check the device's version via" >&2
    echo "the REPL (import sys; sys.implementation), then build/download the" >&2
    echo "matching mpy-cross from:" >&2
    echo "  https://github.com/micropython/micropython/tree/master/mpy-cross" >&2
    exit 1
fi

echo "Using mpy-cross: $(command -v mpy-cross)"
mpy-cross --version || true

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

compile_dir() {
    local src_dir="$1"
    local rel="$2"
    find "$src_dir" -name "*.py" | while read -r src_file; do
        rel_path="${src_file#$REPO_ROOT/}"
        out_path="$BUILD_DIR/${rel_path%.py}.mpy"
        mkdir -p "$(dirname "$out_path")"
        echo "  $rel_path -> ${out_path#$BUILD_DIR/}"
        mpy-cross "$src_file" -o "$out_path"
    done
}

echo ""
echo "Compiling core/ ..."
compile_dir "$REPO_ROOT/core" "core"

echo ""
echo "Compiling drivers/ ..."
compile_dir "$REPO_ROOT/drivers" "drivers"

echo ""
echo "Compiling web/server.py ..."
mkdir -p "$BUILD_DIR/web"
mpy-cross "$REPO_ROOT/web/server.py" -o "$BUILD_DIR/web/server.mpy"

echo ""
echo "Compiling pins.py ..."
mpy-cross "$REPO_ROOT/pins.py" -o "$BUILD_DIR/pins.mpy"

echo ""
echo "Copying non-Python files as-is (boot.py, main.py, web/static/, etc.) ..."
for f in "$REPO_ROOT/boot.py" "$REPO_ROOT/main.py"; do
    if [ -f "$f" ]; then
        cp "$f" "$BUILD_DIR/"
        echo "  $(basename "$f") (kept as .py - see script header)"
    fi
done
if [ -d "$REPO_ROOT/web/static" ]; then
    mkdir -p "$BUILD_DIR/web/static"
    cp -r "$REPO_ROOT/web/static/." "$BUILD_DIR/web/static/"
    echo "  web/static/* (not Python, copied as-is)"
fi

echo ""
echo "Done. Precompiled tree is in: $BUILD_DIR"
echo "Copy its contents to the device's flash (e.g. via mpremote, ampy, or Thonny)."
echo "Remember: /lib (vendored ssd1306/umqtt/aioble) is separate and not handled by this script."
