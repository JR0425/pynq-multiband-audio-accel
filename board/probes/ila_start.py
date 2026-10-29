"""在板子上后台起 ila_prep.py，立刻返回（不等 READY）。

    python ila_start.py --sel 1 --secs 240
"""

import argparse
import sys

import listen_probe as LP

BOARD_PY = "/usr/local/share/pynq-venv/bin/python3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sel", type=int, default=1, choices=[0, 1])
    ap.add_argument("--secs", type=float, default=240.0)
    args = ap.parse_args()

    # 三个 fd 都要重定向掉，否则后台进程会一直占着 SSH 的管道，
    # 客户端 recv 等不到 EOF（2026-10-28 踩过，表现为 socket.timeout，
    # 但板上其实跑得好好的）。
    cmd = ("cd /home/xilinx/dma_exp && nohup %s ila_prep.py --sel %d --secs %.0f "
           "> /tmp/ila_prep.log 2>&1 < /dev/null & disown; sleep 3; cat /tmp/ila_prep.log"
           % (BOARD_PY, args.sel, args.secs))
    rc, o, e = LP.board(cmd)
    print("sel=%d rc=%d\n%s" % (args.sel, rc, o.strip()))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
