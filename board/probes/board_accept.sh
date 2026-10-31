#!/usr/bin/env bash
# 一条命令做完上板验收：推位流 → 寄存器窗口 → 压缩器随参数动 → 双耳两套处方。
#
#   bash board/probes/board_accept.sh            # 全跑（会从耳机放出 440 Hz 正弦）
#   bash board/probes/board_accept.sh --quiet    # 只跑寄存器那一条，不出声
#   bash board/probes/board_accept.sh --skip-push  # 位流已经是这版，跳过推送
#   bash board/probes/board_accept.sh --keep-down  # 验完不停服务（想自己接手动）
#
# 前提：板子开机、能 SSH（192.168.2.99，xilinx/xilinx）、耳机插在板上。
# 输出是全 ASCII 的 PASS/FAIL 表；中文只出现在本脚本的提示里。
set -u
# pwd -W 给的是 D:/... 形式（Python 只认这个）；纯 pwd 在 Git Bash 下是 /d/...，
# 传给 paramiko 会 file-not-found —— 2026-10-29 第一次跑就踩到了。
ROOT="$(cd "$(dirname "$0")/../.." && (pwd -W 2>/dev/null || pwd))"
BD="$ROOT/board/probes"
PY="${PYTHON:-python}"
REMOTE=/home/xilinx/dma_exp
RUN="cd $REMOTE && XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u"

QUIET=0; SKIP_PUSH=0; KEEP_DOWN=0
for a in "$@"; do
  case "$a" in
    --quiet)     QUIET=1 ;;
    --skip-push) SKIP_PUSH=1 ;;
    --keep-down) KEEP_DOWN=1 ;;
    *) echo "未知参数 $a"; exit 2 ;;
  esac
done

cd "$BD" || exit 1
export MSYS_NO_PATHCONV=1

say () { printf '\n=== %s\n' "$1"; }
ssh_ () { "$PY" board_ssh.py "$@"; }

say "0/5 板子在不在"
if ! ssh_ "echo ALIVE; uptime" 2>&1 | grep -q ALIVE; then
    echo "板子没响应。检查：网线、电源、IP 是不是 192.168.2.99"
    exit 1
fi
echo "在线"

if [ "$SKIP_PUSH" = "0" ]; then
    say "1/5 推位流（bit 和 hwh 必须同一次构建）"
    ssh_ --put "$ROOT/board/overlay/fir_pl.bit"  "$REMOTE/fir_pl.bit"  || exit 1
    ssh_ --put "$ROOT/board/overlay/fir_pl.hwh"  "$REMOTE/fir_pl.hwh"  || exit 1
    ssh_ --putb64 pl_regs_check.py   "$REMOTE/pl_regs_check.py"   || exit 1
    ssh_ --putb64 pl_drc_ply_probe.py "$REMOTE/pl_drc_ply_probe.py" || exit 1
    ssh_ --putb64 pl_dual_probe.py   "$REMOTE/pl_dual_probe.py"   || exit 1
    ssh_ --putb64 cap_window_check.py "$REMOTE/cap_window_check.py" || exit 1
else
    say "1/5 跳过推送（--skip-push）"
fi

# 2026-10-30：**必须先停常驻服务**。
# 每个探针里都会 Overlay() 重配一次 PL，而 fir_pl_live 同时在 100 Hz 地读写 PL 寄存器。
# 重配的那几毫秒里 AXI 从机是消失的，live 的访问拿不到响应 → AXI 挂死 → PS 卡死。
#
# 2026-10-31：光 pkill 不够。板上装了 fir-pl-webui.service，它跑的是 pl_supervise.sh，
# 每 30 秒检查一次，发现 live 不在就整套重启 —— pkill 之后最多半分钟 live 又回来了，
# 正好撞上下一个探针的 Overlay，整机重启。两次验收分别死在 3/5 和 4/5 就是这么来的。
# 必须用 systemctl stop，让它不再拉起；再自己确认一遍进程真的没了。
say "1.5/5 停掉常驻服务（探针要重载位流，和 live 抢 AXI 会把板子卡死）"
# 注意 pgrep/pkill 的 -f 会把执行这条命令的 sh 自己也匹配上（它的命令行里就带着
# "fir_pl_live" 这几个字），所以必须写成 [f]ir_pl_live —— 字面串里多了方括号，匹配不到自己。
ssh_ --sudo "systemctl stop fir-pl-webui 2>/dev/null || true; \
             pkill -f '[f]ir_pl_live' || true; pkill -f '[f]ir_pl_webui' || true; sleep 2; \
             pgrep -f '[f]ir_pl_live' >/dev/null && echo LIVE_STILL_UP || echo STOPPED" 2>&1 | tail -2
if ssh_ --sudo "pgrep -f '[f]ir_pl_live' >/dev/null && echo LIVE_UP || echo LIVE_DOWN" 2>&1 | grep -q LIVE_UP; then
    echo "fir_pl_live 还活着，探针会和它抢 AXI 把板子跑死。先 systemctl stop fir-pl-webui 再重跑。"
    exit 1
fi

res_regs=NA; res_drc=NA; res_dual=NA; res_cap=NA
LOG="$ROOT/build/tmp/probes"; mkdir -p "$LOG"

say "2/5 寄存器窗口（ID / 出厂值 / 四段写读 / 斜坡）"
ssh_ --sudo "$RUN pl_regs_check.py" 2>&1 | tee "$LOG/acc_regs.log" | tail -20
grep -q "REGS PASS" "$LOG/acc_regs.log" && res_regs=PASS || res_regs=FAIL

if [ "$QUIET" = "0" ]; then
    say "3/5 压缩器随参数动（会从耳机放测试音，约 3 秒）"
    ssh_ --sudo "$RUN pl_drc_ply_probe.py" 2>&1 | tee "$LOG/acc_drc.log" | tail -24
    grep -q "DRC PLY PROBE PASS" "$LOG/acc_drc.log" && res_drc=PASS || res_drc=FAIL

    say "4/5 双耳两套处方（同样会出声）"
    ssh_ --sudo "$RUN pl_dual_probe.py" 2>&1 | tee "$LOG/acc_dual.log" | tail -24
    grep -q "DUAL PROBE PASS" "$LOG/acc_dual.log" && res_dual=PASS || res_dual=FAIL

    say "5/5 抓取窗口 0x78/0x7C（会出声，约 3 秒）"
    ssh_ --sudo "$RUN cap_window_check.py" 2>&1 | tee "$LOG/acc_cap.log" | tail -18
    grep -q "CAP WINDOW PASS" "$LOG/acc_cap.log" && res_cap=PASS || res_cap=FAIL
else
    say "3/5、4/5、5/5 跳过（--quiet：这几条要出声）"
fi

say "汇总"
printf '  寄存器窗口        %s\n' "$res_regs"
printf '  压缩器随参数动    %s\n' "$res_drc"
printf '  双耳两套处方      %s\n' "$res_dual"
printf '  抓取窗口          %s\n' "$res_cap"
echo
echo "  日志：$LOG/acc_*.log"
echo "  听音还剩三件事（要人）：+30 dB 说话听得清 / 拖增益不咔 / 左右处方不同能听出差别"

if [ "$KEEP_DOWN" = "0" ]; then
    say "恢复常驻服务（live + 网页）"
    ssh_ --sudo "if systemctl cat fir-pl-webui.service >/dev/null 2>&1; then \
                     systemctl start fir-pl-webui; sleep 8; \
                 else cd $REMOTE && sh pl_webui_up.sh; fi; \
                 pgrep -af '[f]ir_pl_(live|webui)'" 2>&1 | tail -4
fi

[ "$res_regs" = "FAIL" ] && exit 1
[ "$res_drc"  = "FAIL" ] && exit 1
[ "$res_dual" = "FAIL" ] && exit 1
[ "$res_cap"  = "FAIL" ] && exit 1
exit 0
