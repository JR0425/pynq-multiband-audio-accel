"""从 Windows 这边跑板上的命令 / 传文件（paramiko，密码登录）。

这台机器上没有 sshpass，公钥也没配，所以走 paramiko。

    python board_ssh.py "uptime"
    python board_ssh.py --sudo "cat /sys/class/thermal/thermal_zone0/temp"
    python board_ssh.py --put 本地文件 /home/xilinx/dma_exp/xxx
    python board_ssh.py --putdir 本地目录 /home/xilinx/dma_exp/

--put 会传完再在板上算一次 md5，和本地对不上就报错退出 —— SFTP 报"成功"
不代表字节真的落对了（10-13 吃过一次：本地改了、板上还是旧的）。
"""

import argparse
import hashlib
import os
import sys

import paramiko

HOST = "192.168.2.99"
USER = "xilinx"
PASS = "xilinx"


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=10,
              allow_agent=False, look_for_keys=False)
    return c


def run(c, cmd, sudo=False):
    if sudo:
        cmd = "echo %s | sudo -S bash -c %s" % (PASS, _q(cmd))
    _in, out, err = c.exec_command(cmd, timeout=600)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    return rc, o, e


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


def md5_local(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_remote(c, path):
    rc, o, e = run(c, "md5sum %s" % _q(path))
    if rc != 0:
        return None
    return o.split()[0]


def put(c, local, remote):
    sftp = c.open_sftp()
    sftp.put(local, remote)
    sftp.close()
    a = md5_local(local)
    b = md5_remote(c, remote)
    if a != b:
        print("MD5 不一致！本地 %s  板上 %s  ← 别用这份文件" % (a, b))
        return False
    print("  %s -> %s  md5 %s ok" % (os.path.basename(local), remote, a))
    return True


def put_b64(c, local, remote):
    """SFTP 的退路：base64 走普通 ssh 会话写文件。

    2026-10-25 连续几次 SFTP open(CREATE) 回 ENOENT，查下来是 Git Bash 把
    `/home/...` 这个参数转成了 `C:/Program Files/Git/home/...`（走 MSYS 的
    参数转换）。所以路径参数一律要配 MSYS_NO_PATHCONV=1；这个函数是另一条
    保险，SFTP 真抽风时也能把文件送上去。"""
    import base64
    with open(local, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    _in, out, err = c.exec_command("base64 -d > %s" % _q(remote), timeout=600)
    for i in range(0, len(data), 65536):
        _in.write(data[i:i + 65536])
        _in.flush()
    _in.channel.shutdown_write()
    rc = out.channel.recv_exit_status()
    if rc != 0:
        print("base64 写失败 rc=%d：%s" % (rc, err.read().decode("utf-8", "replace")))
        return False
    a = md5_local(local)
    b = md5_remote(c, remote)
    if a != b:
        print("MD5 不一致（base64）！本地 %s  板上 %s" % (a, b))
        return False
    print("  %s -> %s  md5 %s ok (base64)" % (os.path.basename(local), remote, a))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="*", help="要执行的命令")
    ap.add_argument("--sudo", action="store_true")
    ap.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"))
    ap.add_argument("--putb64", nargs=2, metavar=("LOCAL", "REMOTE"),
                    help="用 base64 走 ssh 写文件（SFTP 抽风时用）")
    ap.add_argument("--putdir", nargs=2, metavar=("LOCALDIR", "REMOTEDIR"))
    args = ap.parse_args()

    writer = put_b64 if args.putb64 else put
    pair = args.putb64 or args.put

    c = connect()
    try:
        if pair:
            return 0 if writer(c, pair[0], pair[1]) else 1
        if args.putdir:
            src, dst = args.putdir
            good = True
            for name in sorted(os.listdir(src)):
                p = os.path.join(src, name)
                if not os.path.isfile(p):
                    continue
                good = put(c, p, dst.rstrip("/") + "/" + name) and good
            return 0 if good else 1
        rc, o, e = run(c, " ".join(args.cmd), sudo=args.sudo)
        sys.stdout.write(o)
        if e.strip():
            sys.stderr.write(e)
        return rc
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
