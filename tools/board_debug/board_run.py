#!/usr/bin/env python3
"""在板上跑一条命令，把回显原样打回来。

    python tools/board_debug/board_run.py "cd /home/xilinx/dma_exp && ls" [超时秒]

回显按字节写到 stdout，不经过本地控制台码页 —— 板上脚本输出的是 UTF-8，
Windows 控制台默认是 GBK，直接 print 会变成乱码。
"""

import sys
import time

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"


def main():
    cmd = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD, timeout=25,
              banner_timeout=25, auth_timeout=25)

    t0 = time.perf_counter()
    _in, out, _err = c.exec_command(cmd, timeout=timeout, get_pty=False)
    channel = out.channel
    import select
    while True:
        while channel.recv_ready():
            sys.stdout.buffer.write(channel.recv(65536))
            sys.stdout.buffer.flush()
        while channel.recv_stderr_ready():
            sys.stdout.buffer.write(channel.recv_stderr(65536))
            sys.stdout.buffer.flush()
        if channel.exit_status_ready() and not channel.recv_ready():
            break
        select.select([channel], [], [], 0.2)
    rc = channel.recv_exit_status()
    sys.stdout.write("\n[%.1f s] rc = %d\n" % (time.perf_counter() - t0, rc))
    sys.stdout.flush()
    c.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
