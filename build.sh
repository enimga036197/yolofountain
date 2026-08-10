#!/bin/sh
# Build the YoloFountain SIMD core into yolofountain/_yolocore.so (gcc/clang).
# Optional accelerator — the package runs without it.
set -e
CC="${CC:-cc}"
OUT="yolofountain/_yolocore.so"
[ "$(uname)" = "Darwin" ] && OUT="yolofountain/_yolocore.dylib"
$CC -O3 -shared -fPIC -o "$OUT" yolofountain/native/yolo_core.c
echo "Built $OUT"
