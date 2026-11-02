#!/bin/sh
#
# 一键重建：C 仿真 → 导出 IP → Vivado 出 bit/hwh
#
#   ./rebuild.sh            三步全跑
#   ./rebuild.sh csim       只跑 C 仿真（最快，只验算法对不对）
#   ./rebuild.sh ip         从"导出 IP"开始（跳过 csim）
#   ./rebuild.sh bit        只重出 bit/hwh（跳过 csim 和 IP）
#
# 只重出 bit 时不会重新打包 IP —— 改了 C 核就必须至少跑到 ip 这一步。
#
# 工具路径可以用环境变量覆盖（默认是这份仓库当初用的安装位置）：
#   VIVADO_BIN      默认 E:/Xilinx/Vivado/2020.2/bin/vivado.bat
#   VITIS_HLS_BIN   默认 E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat
#
# 在 Git Bash 里跑。详细说明见 build_guide.md。
set -e

REPO=$(cd "$(dirname "$0")" && pwd)
cd "$REPO"

VIVADO_BIN="${VIVADO_BIN:-E:/Xilinx/Vivado/2020.2/bin/vivado.bat}"
VITIS_HLS_BIN="${VITIS_HLS_BIN:-E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat}"

# 出货版本的全部编译参数。换版本时只改这几行，三处保持一致。
# v25 = v21 加 -DFIR_ENV_INDEP=1（一条 DEPENDENCE inter false，解掉合段循环
# 对 env 的假携带依赖）：49 拍 → 36 拍，DSP/BRAM 不变，OOC WNS +1.270 → +1.739。
# 接口没变，所以还是用 v21 那份指令文件。
HLS_LABEL="v25_env_indep"
HLS_DIRECTIVES="build/hls/directives/v21_wdrc.tcl"
HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193 -DFIR_SHIFT_CHAIN=1 -DFIR_ENV_INDEP=1"
export HLS_LABEL HLS_DIRECTIVES HLS_CFLAGS

step="${1:-all}"
case "$step" in
    all|csim|ip|bit) ;;
    *) echo "用法: $0 [all|csim|ip|bit]" >&2; exit 2 ;;
esac

win() {   # 把 Git Bash 路径换成 Windows 路径（cmd /c 不认 /e/...）
    if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi
}

need() {
    [ -f "$1" ] || { echo "找不到 $1" >&2; exit 1; }
}

# MSYS_NO_PATHCONV：不让 MSYS 把 .bat 后面的参数改写成 POSIX 路径
run_hls() {
    MSYS_NO_PATHCONV=1 cmd /c "$(win "$VITIS_HLS_BIN") -f $(win "$1")"
}
run_vivado() {
    MSYS_NO_PATHCONV=1 cmd /c "$(win "$VIVADO_BIN") -notrace -mode batch -source $(win "$1")"
}

if [ "$step" = "all" ] || [ "$step" = "csim" ]; then
    echo "==== 1/3  HLS C 仿真 ===="
    run_hls build/hls/run_csim.tcl
    echo "---- 和整数参考模型逐点比对 ----"
    python sim/model/ref_model_wdrc.py --mode wdrc
fi

if [ "$step" = "all" ] || [ "$step" = "ip" ]; then
    echo "==== 2/3  导出 IP（含 Y2K22 补丁）===="
    run_hls build/hls/run_export_ip.tcl
    need build/vivado/ip_repo/fir_multiband_v1_0/component.xml
fi

if [ "$step" != "csim" ]; then
    echo "==== 3/3  Vivado 整机 overlay ===="
    # 音频 IP 源码不在仓库里（体积大），第一次构建要先取下来
    if [ ! -f build/vivado/ip_repo/audio_codec_ctrl_v1.0/component.xml ]; then
        echo "---- 先取音频 IP 源码 ----"
        python board/overlay/fetch_audio_ip.py
    fi
    run_vivado board/overlay/build_fir.tcl
    need board/overlay/fir.bit
    need board/overlay/fir.hwh
    echo
    echo "产物：board/overlay/fir.bit、board/overlay/fir.hwh"
    echo "上板：把这两个和 board/scripts/*.py 一起拷到板子的 /home/xilinx/jupyter_notebooks/"
fi
