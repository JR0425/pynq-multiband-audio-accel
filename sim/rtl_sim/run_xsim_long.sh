#!/usr/bin/env bash
# 长块（4800 / 8192 点）的 RTL 仿真 + 黄金向量生成。
#
#   bash sim/rtl_sim/run_xsim_long.sh [N] [wav] [start] [tagsuffix]
#   DRC=1 bash sim/rtl_sim/run_xsim_long.sh [N] [wav] [start] [tagsuffix]
#
# 默认 N=4800、音源 data/audio/noisy_voice.wav（48 kHz 单声道真实语音+噪声）、
# 从第 24000 个采样开始截 —— 4800 点正好 100 ms，够长到能暴露块间问题，
# 又是 PLAN 判据 ① 点名的块长。
#
# 第 4 个参数追加在黄金目录名后面，用来并存两份不同的音源。例如
#   DRC=1 bash run_xsim_long.sh 4800 data/audio/ab_board_in.wav 62131 _loud
# 产出 golden_n4800_drc_loud/。注意 tagsuffix 要在 DRC 之后，即
# golden_n<N><_drc><suffix>。
#
# DRC=1 时外壳走 ctrl[17]=1 那条路（四段增益 + WDRC），黄金值由
# 前端吐出来的四段 + Python 定点模型算出来（和 run_xsim_drc4.sh 同一套规则）。
#
# 做三件事：
#   ① 用 tb_fir_mb_fe 跑出这批输入的**黄金输出**（和外壳无关，纯前端）
#   ② 用 tb_fir_rtl_shell 跑同一批输入，逐位比 —— 证明外壳没改变任何一个采样
#   ③ 把黄金对（in_q15.mem + rtl_fe_out.txt）存成 sim/rtl_sim/golden_n<N>/，
#      板上跑 rtl_probe.py 时把这两个文件放同目录就能逐位比
#
# ⚠️ 这里**不覆盖** sim/rtl_sim/in_q15.mem 和 rtl_fe_out.txt（那是 1000 点那份，
#    别的脚本还在用），只用工作目录和 golden_n<N>/ 子目录。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"

N="${1:-4800}"
WAV="${2:-$ROOT/data/audio/noisy_voice.wav}"
START="${3:-24000}"
SUF="${4:-}"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

WORK="$SIM/xsim_work_long_$N$SUF"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
[ -f "$WAV" ] || { echo "找不到音源 $WAV"; exit 1; }
for E in 500 1000 2000; do
    [ -f "$ROOT/src/rtl/coeffs_lp${E}_n193.mem" ] || {
        echo "缺 coeffs_lp${E}_n193.mem"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

echo "### 取 $N 点输入（$WAV 从第 $START 个采样起）"
python - "$WAV" "$START" "$N" <<'PY' || exit 1
import sys, wave
wav, start, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
w = wave.open(wav)
assert w.getsampwidth() == 2, "要 16 位 PCM"
ch = w.getnchannels()
w.setpos(start)
raw = w.readframes(n)
v = [int.from_bytes(raw[i:i + 2], "little", signed=True)
     for i in range(0, len(raw), 2 * ch)]
if len(v) < n:
    sys.exit("音源不够长：只拿到 %d 点" % len(v))
with open("in_q15.mem", "w") as f:
    for s in v[:n]:
        f.write("%04x\n" % (s & 0xFFFF))
print("  点数 %d  峰值 %d" % (n, max(abs(x) for x in v[:n])))
PY

cp "$ROOT/src/rtl/coeffs_lp500_n193.mem"  coeffs_lp500.mem
cp "$ROOT/src/rtl/coeffs_lp1000_n193.mem" coeffs_lp1000.mem
cp "$ROOT/src/rtl/coeffs_lp2000_n193.mem" coeffs_lp2000.mem

run_sim() {   # $1 = 顶层模块名  $2 = tb 源文件  $3 = 日志前缀
    echo "### 编译 $1"
    MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
        -i "$(cygpath -w "$SIM")" \
        "$(cygpath -w "$ROOT/src/rtl/fir_mb_fe.v")" \
        "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
        "$(cygpath -w "$ROOT/src/rtl/fir_rtl_shell.v")" \
        "$(cygpath -w "$2")" > "${3}_xvlog.log" 2>&1
    grep -q "analyzing module" "${3}_xvlog.log" || {
        echo "xvlog 没跑起来，见 $WORK/${3}_xvlog.log"; tail -20 "${3}_xvlog.log"; exit 1; }
    grep -iE "^ERROR" "${3}_xvlog.log" && { echo "xvlog 有错"; exit 1; }

    MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
        "$1" -s "snap_$1" --debug typical > "${3}_xelab.log" 2>&1
    grep -iE "^ERROR" "${3}_xelab.log" && { echo "xelab 有错"; tail -20 "${3}_xelab.log"; exit 1; }

    MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" "snap_$1" -R $SIM_ARG \
        > "${3}_xsim.log" 2>&1
    grep -E "\[tb\]" "${3}_xsim.log" | sed 's/^/    /'
}

DRC="${DRC:-0}"
SIM_ARG=""
TAG=""
if [ "$DRC" = "1" ]; then
    SIM_ARG="-testplusarg DRC"
    TAG="_drc"
fi
GOLD="$SIM/golden_n${N}${TAG}${SUF}"

echo
echo "=== ① 前端黄金输出（tb_fir_mb_fe）==="
run_sim tb_fir_mb_fe "$SIM/tb_fir_mb_fe.v" fe
grep -q "ERROR" fe_xsim.log && { echo "前端仿真报错"; exit 1; }
mv rtl_fe_out.txt golden_fe.txt
# 前端 tb 还会写一堆中间量，这里只要最终输出，其余挪开免得和外壳那次混淆
for f in rtl_fe_*.mem; do [ -e "$f" ] && mv "$f" "fe_$f"; done

echo
echo "=== ② 外壳走同一批输入（tb_fir_rtl_shell）==="
run_sim tb_fir_rtl_shell "$SIM/tb_fir_rtl_shell.v" shell
grep -q "\[tb\] SHELL PASS" shell_xsim.log || {
    echo "外壳仿真没过，见 $WORK/shell_xsim.log"; tail -30 shell_xsim.log; exit 1; }

echo
echo "=== ③ 逐位比 ==="
if [ "$DRC" = "1" ]; then
    # 黄金值 = 前端四段 + 和 HLS 同一套定点规则（和 run_xsim_drc4.sh 一致）
    python - <<'PY' || exit 1
import os
DW = 24
MAXD, MIND = (1 << 23) - 1, -(1 << 23)
THR, RAT, ATK, REL, GAIN = 419456, 2936064, 17408, 896, 256
OMR = (1 << 22) - RAT


def sat24(v):
    return MIND if v < MIND else (MAXD if v > MAXD else v)


def sat16(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


# 2026-10-30 末级从"硬饱和"改成六段折线软膝限幅（见 fir_drc4.v）。
# 和 run_xsim_drc4.sh 里那份是同一套规则；那边 10-30 就改了，这边漏了，
# 导致黄金向量还是硬饱和的版本，凡过载的采样都会和硬件对不上。
LIM_T = 22937


def lim16(u):
    if -LIM_T <= u <= LIM_T:
        return u
    e = abs(u) - LIM_T
    if e < 6554:
        c = e >> 1
    elif e < 9831:
        c = 3277 + ((e - 6554) >> 2)
    elif e < 26215:
        c = 4096 + ((e - 9831) >> 4)
    elif e < 75311:
        c = 5120 + ((e - 26215) >> 6)
    elif e < 466615:
        c = 5888 + ((e - 75311) >> 8)
    else:
        c = 7416 + ((e - 466615) >> 12)
    y = LIM_T + c
    return -y if u < 0 else y


def s40(h):
    v = int(h.strip(), 16)
    return v - (1 << 40) if v >= (1 << 39) else v


bands = []
for b in range(4):
    with open("fe_rtl_fe_band%d.mem" % b) as f:
        bands.append([s40(s) for s in f if s.strip()])
L = len(bands[0])
assert all(len(x) == L for x in bands), "四段长度不一致"

env = [0] * 4
with open("golden_drc.txt", "w") as fo:
    for i in range(L):
        acc = 0
        for b in range(4):
            gb = sat24(bands[b][i] >> 10)
            gb = sat24((gb * GAIN) >> 8)
            a = sat24(-gb if gb < 0 else gb)
            diff = a - env[b]
            step = ATK if diff > 0 else REL
            e = sat24(((env[b] << 22) + diff * step) >> 22)
            if e < 0:
                e = 0
            env[b] = e
            gr = sat24(((e - THR) * OMR) >> 22) if e > THR else 0
            a2 = a - gr
            if a2 < 0:
                a2 = 0
            yv = -a2 if gb < 0 else a2
            acc += yv << 10
        fo.write("%d\n" % lim16(acc >> 17))
print("  黄金值已生成 golden_drc.txt（%d 点）" % L)
PY
    GOLDEN="golden_drc.txt"
else
    GOLDEN="golden_fe.txt"
fi

python - "$GOLDEN" <<'PY'
import sys
gname = sys.argv[1]
a = [int(x) for x in open("shell_out.txt") if x.strip()]
b = [int(x) for x in open(gname) if x.strip()]
if len(a) != len(b):
    print("长度不一致 %d vs %d" % (len(a), len(b))); sys.exit(1)
bad = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
print("  比了 %d 点，不同 %d 点" % (len(a), len(bad)))
for i in bad[:5]:
    print("  [%d] shell=%d golden=%d" % (i, a[i], b[i]))
sys.exit(1 if bad else 0)
PY
RC=$?

mkdir -p "$GOLD"
cp in_q15.mem "$GOLD/in_q15.mem"
cp "$GOLDEN" "$GOLD/rtl_fe_out.txt"

echo
if [ $RC -eq 0 ]; then
    echo "### n=$N 外壳输出 == 黄金输出（逐位，模式 ${DRC} = $([ "$DRC" = 1 ] && echo 'fe+增益+WDRC' || echo '仅前端')）"
    echo "### 黄金对已存：$GOLD/"
    echo "### 上板：把 golden_n$N${TAG}${SUF}/in_q15.mem 和 rtl_fe_out.txt 和 fir_rtl.bit/.hwh 放同一目录"
else
    echo "### n=$N 对不上"
fi
exit $RC
