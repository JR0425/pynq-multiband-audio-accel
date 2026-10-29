#!/usr/bin/env bash
# 一条命令跑完不需要板子的全部验收。
#
#   bash sim/run_all_checks.sh
#
# 五项，任何一项挂了最后都返回非 0：
#   1) DRC 增益/四参数/斜坡 三组仿真（xsim）
#   2) 寄存器文件单元测试（xsim）
#   3) 全 RTL 语法检查（xvlog）
#   4) PL 直连后端自检（纯 Python，不加载 overlay）
#   5) 网页第三档自检（起临时服务，走 HTTP）
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

LOG=build/tmp/probes
mkdir -p "$LOG"
rc=0

PY=python
command -v python3 >/dev/null 2>&1 && PY=python3

note_fail () {           # note_fail <名字> <日志> <期望字符串>
    echo "  FAIL"
    echo "    $1 没出现「$3」"
    echo "    日志: $2"
    tail -6 "$2" | sed 's/^/    /'
    rc=1
}

check () {               # check <说明> <日志> <期望字符串>；说明已经由 printf 打过
    if grep -q "$3" "$2" 2>/dev/null; then
        echo "OK"
    else
        note_fail "$1" "$2" "$3"
    fi
}

echo "=== PL 直连 · 不需要板子的验收 ==="
echo

printf '%-34s' "1/5 三组 DRC 仿真"
bash sim/rtl_sim/run_xsim_gain.sh > "$LOG/all_1_sim.log" 2>&1
check "三组 DRC 仿真" "$LOG/all_1_sim.log" "三组仿真都通过"

printf '%-34s' "2/5 寄存器文件单元测试"
bash sim/rtl_sim/run_xsim_regs.sh > "$LOG/all_2_regs.log" 2>&1
check "寄存器单元测试" "$LOG/all_2_regs.log" "PASS 寄存器文件全部通过"

printf '%-34s' "3/5 RTL 语法检查"
bash sim/rtl_sim/run_lint_pl.sh > "$LOG/all_3_lint.log" 2>&1
check "RTL 语法检查" "$LOG/all_3_lint.log" "LINT OK"

printf '%-34s' "4/5 后端自检（fir_pl_live）"
PYTHONIOENCODING=utf-8 $PY board/scripts/fir_pl_live.py --selftest > "$LOG/all_4_live.log" 2>&1
check "后端自检" "$LOG/all_4_live.log" "SELFTEST PASS"

printf '%-34s' "5/5 网页第三档自检"
PYTHONIOENCODING=utf-8 $PY board/scripts/fir_pl_webui.py --selftest > "$LOG/all_5_webui.log" 2>&1
check "网页第三档自检" "$LOG/all_5_webui.log" "SELFTEST PASS"

echo
if [ $rc -eq 0 ]; then
    echo "全部通过。板上那两条另算：board/probes/pl_regs_check.py、pl_drc_probe.py / pl_drc_ply_probe.py"
else
    echo "有失败，日志在 $LOG/all_*.log"
fi
exit $rc
