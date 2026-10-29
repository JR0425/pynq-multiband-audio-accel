"""在板子上后台启动 ila_prep.py，占住板子放音；PC 侧随后用 JTAG 抓波形。

    python ila_kick.py --sel 1 --secs 60
"""

import argparse
import sys
import time

import listen_probe as LP

BOARD_PY = "/usr/local/share/pynq-venv/bin/python3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sel", type=int, default=0, choices=[0, 1])
    ap.add_argument("--secs", type=float, default=60.0)
    args = ap.parse_args()

    cmd = ("cd /home/xilinx/dma_exp && nohup %s ila_prep.py --sel %d --secs %.0f "
           "> /tmp/ila_prep.log 2>&1 & sleep 2; echo KICKED" % (BOARD_PY, args.sel, args.secs))
    rc, o, e = LP.board(cmd)
    print("kick rc=%d  %s" % (rc, o.strip()))
    if rc != 0:
        print(e[:400])

    # 等板上把 overlay 加载完、codec 配好
    for _ in range(20):
        time.sleep(1.5)
        rc, o, e = LP.board("cat /tmp/ila_prep.log 2>/dev/null || true")
        if "READY" in o:
            print(o.strip())
            return 0
        if "Error" in o or "Traceback" in o:
            print("板上出错：\n" + o)
            return 1
    print("等 READY 超时，板上日志：\n" + o)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
