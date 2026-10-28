#!/usr/bin/env bash
# 增益端口化 + 斜坡的两组仿真
#   1) tb_drc_gain  ：与改动前的 fir_drc4 逐位对拍（gain=256），再验 +18 dB 生效与饱和
#   2) tb_gain_ramp ：斜坡单调、无阶跃、能到底、clr 直接对齐
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"
for f in "$XV" "$XE" "$XS"; do [ -f "$f" ] || { echo "找不到 $f"; exit 1; }; done

rc=0

run_one () {
    name="$1"; top="$2"; files="$3"
    work="$SIM/xsim_work_$name"
    rm -rf "$work"; mkdir -p "$work"; cd "$work" || exit 1
    MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" $files > xvlog.log 2>&1
    if grep -qiE "^ERROR" xvlog.log; then echo "[$name] xvlog 失败"; tail -20 xvlog.log; return 1; fi
    MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" "$top" -s "tb_$name" > xelab.log 2>&1
    if grep -qiE "^ERROR" xelab.log; then echo "[$name] xelab 失败"; tail -20 xelab.log; return 1; fi
    MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" "tb_$name" -R > xsim.log 2>&1
    grep -E "===|PASS|FAIL|失配|回退|TIMEOUT|收敛|最大单步" xsim.log | sed "s/^/  /"
    if grep -q "=== 全部通过 ===" xsim.log; then return 0; else return 1; fi
}

echo "########## 1/2  DRC 增益端口化（逐位对拍） ##########"
W() { cygpath -w "$1"; }
files="$(W "$ROOT/src/rtl/fir_drc4.v") $(W "$SIM/ref/fir_drc4_ref.v") $(W "$SIM/tb_drc_gain.v")"
run_one drc_gain tb_drc_gain "$files" || rc=1

echo
echo "########## 2/2  增益斜坡 ##########"
files="$(W "$ROOT/src/rtl/fir_gain_ramp.v") $(W "$SIM/tb_gain_ramp.v")"
run_one gain_ramp tb_gain_ramp "$files" || rc=1

echo
[ $rc -eq 0 ] && echo "===== 两组仿真都通过 =====" || echo "===== 有失败，看上面 ====="
exit $rc
