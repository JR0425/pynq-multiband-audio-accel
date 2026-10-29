#!/usr/bin/env bash
# 一条命令做完上板验收：推位流 → 寄存器窗口 → 压缩器随参数动 → 双耳两套处方。
#
#   bash tools/board_debug/board_accept.sh            # 全跑（会从耳机放出 440 Hz 正弦）
#   bash tools/board_debug/board_accept.sh --quiet    # 只跑寄存器那一条，不出声
#   bash tools/board_debug/board_accept.sh --skip-push  # 位流已经是这版，跳过推送
#
# 前提：板子开机、能 SSH（192.168.2.99，xilinx/xilinx）、耳机插在板上。
# 输出是全 ASCII 的 PASS/FAIL 表；中文只出现在本脚本的提示里。
set -u
# pwd -W 给的是 D:/... 形式（Python 只认这个）；纯 pwd 在 Git Bash 下是 /d/...，
# 传给 paramiko 会 file-not-found —— 2026-10-29 第一次跑就踩到了。
ROOT="$(cd "$(dirname "$0")/../.." && (pwd -W 2>/dev/null || pwd))"
BD="$ROOT/tools/board_debug"
PY="${PYTHON:-python}"
REMOTE=/home/xilinx/dma_exp
RUN="cd $REMOTE && XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u"

QUIET=0; SKIP_PUSH=0
for a in "$@"; do
  case "$a" in
    --quiet)     QUIET=1 ;;
    --skip-push) SKIP_PUSH=1 ;;
    *) echo "未知参数 $a"; exit 2 ;;
  esac
done

cd "$BD" || exit 1
export MSYS_NO_PATHCONV=1

say () { printf '\n=== %s\n' "$1"; }
ssh_ () { "$PY" board_ssh.py "$@"; }

say "0/4 板子在不在"
if ! ssh_ "echo ALIVE; uptime" 2>&1 | grep -q ALIVE; then
    echo "板子没响应。检查：网线、电源、IP 是不是 192.168.2.99"
    exit 1
fi
echo "在线"

if [ "$SKIP_PUSH" = "0" ]; then
    say "1/4 推位流（bit 和 hwh 必须同一次构建）"
    ssh_ --put "$ROOT/board/overlay/fir_pl.bit"  "$REMOTE/fir_pl.bit"  || exit 1
    ssh_ --put "$ROOT/board/overlay/fir_pl.hwh"  "$REMOTE/fir_pl.hwh"  || exit 1
    ssh_ --putb64 pl_regs_check.py   "$REMOTE/pl_regs_check.py"   || exit 1
    ssh_ --putb64 pl_drc_ply_probe.py "$REMOTE/pl_drc_ply_probe.py" || exit 1
    ssh_ --putb64 pl_dual_probe.py   "$REMOTE/pl_dual_probe.py"   || exit 1
else
    say "1/4 跳过推送（--skip-push）"
fi

res_regs=NA; res_drc=NA; res_dual=NA
LOG="$BD/_tmp"; mkdir -p "$LOG"

say "2/4 寄存器窗口（ID / 出厂值 / 四段写读 / 斜坡）"
ssh_ --sudo "$RUN pl_regs_check.py" 2>&1 | tee "$LOG/acc_regs.log" | tail -20
grep -q "REGS PASS" "$LOG/acc_regs.log" && res_regs=PASS || res_regs=FAIL

if [ "$QUIET" = "0" ]; then
    say "3/4 压缩器随参数动（会从耳机放 440 Hz，约 3 秒）"
    ssh_ --sudo "$RUN pl_drc_ply_probe.py" 2>&1 | tee "$LOG/acc_drc.log" | tail -24
    grep -q "DRC PLY PROBE PASS" "$LOG/acc_drc.log" && res_drc=PASS || res_drc=FAIL

    say "4/4 双耳两套处方（同样会出声）"
    ssh_ --sudo "$RUN pl_dual_probe.py" 2>&1 | tee "$LOG/acc_dual.log" | tail -24
    grep -q "DUAL PROBE PASS" "$LOG/acc_dual.log" && res_dual=PASS || res_dual=FAIL
else
    say "3/4、4/4 跳过（--quiet：这两条要出声）"
fi

say "汇总"
printf '  寄存器窗口        %s\n' "$res_regs"
printf '  压缩器随参数动    %s\n' "$res_drc"
printf '  双耳两套处方      %s\n' "$res_dual"
echo
echo "  日志：$LOG/acc_*.log"
echo "  听音还剩三件事（要人）：+30 dB 说话听得清 / 拖增益不咔 / 左右处方不同能听出差别"

[ "$res_regs" = "FAIL" ] && exit 1
[ "$res_drc"  = "FAIL" ] && exit 1
[ "$res_dual" = "FAIL" ] && exit 1
exit 0
