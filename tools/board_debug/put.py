# ⚠ 2026-10-16 收尾补:主机侧小工具。走 base64 把文件推到板上,因为 SFTP 那条路不通。
#   `--exec` 会加可执行位。next_round.py 里的 push_file() 用的是同一条路。
"""往板子上放一个小文件。走 base64,因为 SFTP 那条路(board_put.py)不通。

用法: python put.py <本地文件> <板上路径> [--exec]
"""

import base64
import sys

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"


def main():
    local, remote = sys.argv[1], sys.argv[2]
    make_exec = "--exec" in sys.argv
    with open(local, "rb") as f:
        raw = f.read()
    raw = raw.replace(b"\r\n", b"\n")                 # 别把 CRLF 带上板
    b64 = base64.b64encode(raw)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD, timeout=25,
              banner_timeout=25, auth_timeout=25)
    _in, out, err = c.exec_command("base64 -d > %s" % remote, timeout=60)
    _in.write(b64)
    _in.channel.shutdown_write()
    rc = out.channel.recv_exit_status()
    e = err.read().decode("utf-8", "replace")
    print("put rc=%d %s" % (rc, e.strip()))

    if rc == 0:
        cmd = "chmod %s %s; ls -la %s; md5sum %s" % (
            "+x" if make_exec else "644", remote, remote, remote)
        _in, out, _err = c.exec_command(cmd, timeout=30)
        print(out.read().decode("utf-8", "replace").strip())
    c.close()
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
