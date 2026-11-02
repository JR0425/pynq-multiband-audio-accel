#!/usr/bin/env bash
# 给报告出波形图用的两次仿真（不做判据，判据在各自的 tb 里）：
#
#   bash sim/rtl_sim/run_xsim_wave.sh [N]
#
#   A. 前端 3 拍节奏 —— tb_fir_mb_fe 带 -testplusarg WAVE，只 dump 几根信号，
#      产物 xsim_work_wave_fe/wave.vcd（N 个采样，默认 200）
#      取 200 而不是几十：群延迟 96 采样，短于这个数输出全是 0，图上没东西可看。
#   B. 软膝限幅 —— tb_lim_wave，产物 xsim_work_wave_lim/lim_wave_inout.txt
#      和 lim_curve.txt
#   C. I2S 帧时序 —— tb_i2s_duplex 带 -testplusarg WAVE，产物
#      xsim_work_wave_i2s/wave.vcd
#
# 出图分两条路：
#
#   波形窗口截图（报告用的图 1、图 3）
#       cd sim/rtl_sim/xsim_work_wave_fe && xsim snap_fe -gui
#       Tcl Console: open_wave_config <repo>/sim/rtl_sim/wave_fe.wcfg
#     I2S 同理，换 xsim_work_wave_i2s / snap_i2s / wave_i2s.wcfg。两个 wcfg 里
#     存了时间范围、光标位置和列宽，打开就是报告里那张图的视角。
#
#   脚本画图（图 2，以及 1/3 的备用版本）
#       python src/python/plot_waveform_figures.py
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"

N="${1:-200}"
WAV="$ROOT/data/audio/noisy_voice.wav"
START=24000

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do [ -f "$f" ] || { echo "找不到 $f"; exit 1; }; done
[ -f "$WAV" ] || { echo "找不到音源 $WAV"; exit 1; }

# ---------------------------------------------------------------- A. 前端
WA="$SIM/xsim_work_wave_fe"
rm -rf "$WA"; mkdir -p "$WA"; cd "$WA" || exit 1

python - "$WAV" "$START" "$N" <<'PY' || exit 1
import sys, wave
wav, start, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
w = wave.open(wav)
ch = w.getnchannels()
w.setpos(start)
raw = w.readframes(n)
v = [int.from_bytes(raw[i:i + 2], "little", signed=True)
     for i in range(0, len(raw), 2 * ch)]
with open("in_q15.mem", "w") as f:
    for s in v:
        f.write("%04x\n" % (s & 0xFFFF))
print("  取 %d 点，峰值 %d" % (n, max(abs(x) for x in v)))
PY

for E in 500 1000 2000; do cp "$ROOT/src/rtl/coeffs_lp${E}_n193.mem" "coeffs_lp${E}.mem"; done

echo "### A. 前端（tb_fir_mb_fe + WAVE）"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    -i "$(cygpath -w "$SIM")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_mb_fe.v")" \
    "$(cygpath -w "$SIM/tb_fir_mb_fe.v")" > fe_xvlog.log 2>&1
grep -q "analyzing module" fe_xvlog.log || { echo "xvlog 没跑起来"; tail -20 fe_xvlog.log; exit 1; }
grep -iE "^ERROR" fe_xvlog.log && { echo "xvlog 有错"; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" tb_fir_mb_fe -s snap_fe --debug typical > fe_xelab.log 2>&1
grep -iE "^ERROR" fe_xelab.log && { tail -20 fe_xelab.log; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" snap_fe -R -testplusarg WAVE > fe_xsim.log 2>&1
grep -E "\[tb\]" fe_xsim.log | sed 's/^/    /'
[ -s wave.vcd ] || { echo "没生成 wave.vcd"; exit 1; }
ls -l wave.vcd | awk '{print "    wave.vcd " $5 " 字节"}'

# ---------------------------------------------------------------- B. 限幅
WB="$SIM/xsim_work_wave_lim"
rm -rf "$WB"; mkdir -p "$WB"; cd "$WB" || exit 1

echo "### B. 软膝限幅（tb_lim_wave）"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
    "$(cygpath -w "$SIM/tb_lim_wave.v")" > lim_xvlog.log 2>&1
grep -q "analyzing module" lim_xvlog.log || { echo "xvlog 没跑起来"; tail -20 lim_xvlog.log; exit 1; }
grep -iE "^ERROR" lim_xvlog.log && { echo "xvlog 有错"; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" tb_lim_wave -s snap_lim --debug typical > lim_xelab.log 2>&1
grep -iE "^ERROR" lim_xelab.log && { tail -20 lim_xelab.log; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" snap_lim -R > lim_xsim.log 2>&1
grep -E "\[tb\]" lim_xsim.log | sed 's/^/    /'
wc -l lim_wave_inout.txt lim_curve.txt 2>/dev/null | sed 's/^/    /'

echo
echo "### C. I2S 帧时序（tb_i2s_duplex + WAVE）"
WC="$SIM/xsim_work_wave_i2s"
rm -rf "$WC"; mkdir -p "$WC"; cd "$WC" || exit 1

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/axis_fifo.v")" \
    "$(cygpath -w "$ROOT/src/rtl/i2s_duplex.v")" \
    "$(cygpath -w "$SIM/tb_i2s_duplex.v")" > i2s_xvlog.log 2>&1
grep -q "analyzing module" i2s_xvlog.log || { echo "xvlog 没跑起来"; tail -20 i2s_xvlog.log; exit 1; }
grep -iE "^ERROR" i2s_xvlog.log && { echo "xvlog 有错"; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" tb_i2s_duplex -s snap_i2s --debug typical > i2s_xelab.log 2>&1
grep -iE "^ERROR" i2s_xelab.log && { tail -20 i2s_xelab.log; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" snap_i2s -R -testplusarg WAVE > i2s_xsim.log 2>&1
grep -E "RESULT|captured words" i2s_xsim.log | sed 's/^/    /'
[ -s wave.vcd ] || { echo "没生成 wave.vcd"; exit 1; }

echo
echo "### 出图：python src/python/plot_waveform_figures.py"