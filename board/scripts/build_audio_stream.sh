#!/bin/sh
#
# 在板上编 libaudio_stream.so —— 给 PYNQ-Z2 的音频 IP 补一条连续播放/录音的通路。
#
# 为什么要在**板上**编：这东西要直连 /dev/uio0 和 /dev/i2c-1，是板子上的东西。
# 好消息是板上自带 gcc/g++（PetaLinux 里的 9.3.0），**不用交叉编译**。
#
# 它只依赖 pynq 包里那三个跟着装好的 C 源文件（i2cps.c / uio.c /
# audio_adau1761.cpp），以及本项目自己的 sources/audio_stream.cpp。
#
#   sh build_audio_stream.sh                # 编到脚本所在目录
#   OUT=/tmp sh build_audio_stream.sh       # 换个输出目录
#   PYNQ_LIB=<...>/pynq/lib sh build_audio_stream.sh   # 手动指定 pynq 位置
#
set -e

# ---- 找 pynq 的 lib 目录（就是那个放着 libaudio.so 的地方）----
if [ -z "$PYNQ_LIB" ]; then
    for d in /usr/local/share/pynq-venv/lib/python3.*/site-packages/pynq/lib; do
        if [ -d "$d" ]; then
            PYNQ_LIB=$d
        fi
    done
fi

if [ -z "$PYNQ_LIB" ] || [ ! -f "$PYNQ_LIB/_pynq/_audio/audio_adau1761.cpp" ]; then
    echo "找不到 pynq 的音频 C 源码。手动指一下："
    echo "  PYNQ_LIB=/usr/local/share/pynq-venv/lib/python3.8/site-packages/pynq/lib sh $0"
    exit 1
fi
AUDIO_SRC="$PYNQ_LIB/_pynq/_audio"

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE}

if [ ! -f "$HERE/audio_stream.cpp" ]; then
    echo "找不到 $HERE/audio_stream.cpp —— 这个脚本得和它放在一起。"
    exit 1
fi

CC=${CC:-gcc}
CXX=${CXX:-g++}
CFLAGS="-fPIC -O2 -Wall"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "pynq 音频源码： $AUDIO_SRC"
echo "本项目源码：   $HERE/audio_stream.cpp"
echo "输出：         $OUT/libaudio_stream.so"
echo

# 分着编：i2cps.c / uio.c 是 C，另两个是 C++。
# （audio_adau1761.cpp 必须一起编 —— 我们要用它的 write_audio_reg 和
#   那堆 R## 寄存器常量；它自己又要用 uio.c 的 setUIO。）
$CC  $CFLAGS -I"$AUDIO_SRC" -c "$AUDIO_SRC/i2cps.c"            -o "$TMP/i2cps.o"
$CC  $CFLAGS -I"$AUDIO_SRC" -c "$AUDIO_SRC/uio.c"              -o "$TMP/uio.o"
$CXX $CFLAGS -I"$AUDIO_SRC" -c "$AUDIO_SRC/audio_adau1761.cpp" -o "$TMP/audio_adau1761.o"
$CXX $CFLAGS -I"$AUDIO_SRC" -c "$HERE/audio_stream.cpp"        -o "$TMP/audio_stream.o"

$CXX -shared -o "$OUT/libaudio_stream.so" \
     "$TMP/i2cps.o" "$TMP/uio.o" "$TMP/audio_adau1761.o" "$TMP/audio_stream.o"

echo "编好了："
ls -l "$OUT/libaudio_stream.so"
echo
echo "导出的符号（应该有 stream_begin/block/end 和 capture_begin/block/end）："
nm -D --defined-only "$OUT/libaudio_stream.so" | grep -E "stream_|capture_" || true
