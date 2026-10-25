#!/usr/bin/env bash
# fir_drc4（四段增益 + WDRC）的单元验证。
#
#   bash sim/rtl_sim/run_xsim_drc4.sh
#
# 输入直接用 RTL 前端吐出来的四段（sim/rtl_sim/rtl_fe_band0..3.mem），
# 所以这一测就是「接在 fir_mb_fe 后面」的真实数据，不是造出来的激励。
# 黄金值用和 HLS 同一套定点规则在 Python 里重算一遍，逐位比。
#
# 前提：先跑过 run_xsim_mb.sh（它会生成 rtl_fe_band*.mem）。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_drc4"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
for b in 0 1 2 3; do
    [ -f "$SIM/rtl_fe_band$b.mem" ] || {
        echo "缺 rtl_fe_band$b.mem —— 先跑 bash sim/rtl_sim/run_xsim_mb.sh"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

for b in 0 1 2 3; do cp "$SIM/rtl_fe_band$b.mem" "rtl_fe_band$b.mem"; done

echo "### 编译"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
    "$(cygpath -w "$SIM/tb_fir_drc4.v")" > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; tail -20 xvlog.log; exit 1; }
grep -iE "^ERROR" xvlog.log && { echo "xvlog 有错"; exit 1; }

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_fir_drc4 -s tb_sim_drc4 --debug typical > xelab.log 2>&1
grep -iE "^ERROR" xelab.log && { echo "xelab 有错"; tail -20 xelab.log; exit 1; }

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_drc4 -R > xsim.log 2>&1
grep -E "\[tb\]" xsim.log | sed 's/^/    /'
grep -q "\[tb\] DRC4 PASS" xsim.log || {
    echo "仿真没过，见 $WORK/xsim.log"; tail -30 xsim.log; exit 1; }

echo
echo "### 和 Python 黄金模型逐位比"
cd "$ROOT" || exit 1
PYTHONIOENCODING=utf-8 python - "$WORK/drc_out.txt" "$SIM" <<'PY'
import sys, os

out_path, sim = sys.argv[1], sys.argv[2]

# ---------------- 和 src/hls/fir_types.h 对齐的定点规则 ----------------
DW = 24
MAXD, MIND = (1 << 23) - 1, -(1 << 23)
THR, RAT, ATK, REL, GAIN = 419456, 2936064, 17408, 896, 256
OMR = (1 << 22) - RAT


def sat24(v):
    return MIND if v < MIND else (MAXD if v > MAXD else v)


def sat16(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


def s40(h):
    v = int(h.strip(), 16)
    return v - (1 << 40) if v >= (1 << 39) else v


bands = []
for b in range(4):
    with open(os.path.join(sim, "rtl_fe_band%d.mem" % b)) as f:
        bands.append([s40(s) for s in f if s.strip()])
L = len(bands[0])
assert all(len(x) == L for x in bands), "四段长度不一致"

env = [0] * 4
golden = []
for i in range(L):
    acc = 0
    for b in range(4):
        gb = sat24(bands[b][i] >> 10)          # acc_t -> drc_t
        gb = sat24((gb * GAIN) >> 8)           # * gain_t -> drc_t
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
    golden.append(sat16(acc >> 17))

got = [int(s) for s in open(out_path) if s.strip()]
if len(got) != len(golden):
    print("长度不一致：RTL %d，模型 %d" % (len(got), len(golden)))
    sys.exit(1)

bad = [(i, g, k) for i, (g, k) in enumerate(zip(got, golden)) if g != k]
print("比了 %d 点，不同 %d 点" % (len(got), len(bad)))
for i, g, k in bad[:8]:
    print("  [%d] rtl=%d model=%d  差 %d" % (i, g, k, g - k))
sys.exit(1 if bad else 0)
PY
RC=$?

echo
if [ $RC -eq 0 ]; then echo "### fir_drc4 与 HLS 定点规则逐位一致"; else echo "### fir_drc4 对不上"; fi
exit $RC
