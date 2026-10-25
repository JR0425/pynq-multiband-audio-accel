"""把 fir_dma overlay + 探针脚本推到板上 /home/xilinx/dma_exp/

用 base64 走 exec_command（SFTP 那条路在这块板上不通，见 put.py 的注释）。
传完逐个核对 md5 —— 这步不能省，SFTP 曾经报成功但文件是坏的。

用法：
    python tools/board_debug/push_dma_exp.py
"""

import base64
import hashlib
import os
import sys

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"
REMOTE_DIR = "/home/xilinx/dma_exp"

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILES = [
    ("board/overlay/fir_dma.bit", "fir_dma.bit"),
    ("board/overlay/fir_dma.hwh", "fir_dma.hwh"),
    ("board/scripts/dma_audio_probe.py", "dma_audio_probe.py"),
]
# 额外文件按仓库相对路径追加，比如 tools/board_debug/dma_audio_diag.py
FILES += [(p, os.path.basename(p)) for p in sys.argv[1:]]


def md5(data):
    return hashlib.md5(data).hexdigest()


def push(c, local, remote):
    with open(local, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw)

    stdin, stdout, stderr = c.exec_command("base64 -d > %s" % remote, timeout=600)
    for i in range(0, len(b64), 65536):
        stdin.write(b64[i:i + 65536])
        stdin.flush()
    stdin.channel.shutdown_write()
    rc = stdout.channel.recv_exit_status()
    if rc != 0:
        print("  推送失败 rc=%d %s" % (rc, stderr.read().decode("utf-8", "replace")))
        return False, None

    _in, out, _err = c.exec_command("md5sum %s" % remote, timeout=120)
    line = out.read().decode("utf-8", "replace").strip()
    got = line.split()[0] if line else ""
    return got == md5(raw), got


def main():
    missing = [l for l, _ in FILES if not os.path.exists(os.path.join(REPO, l))]
    if missing:
        print("ERROR: 缺文件 %s" % missing)
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD, timeout=25,
              banner_timeout=25, auth_timeout=25)

    _in, out, _err = c.exec_command("mkdir -p %s" % REMOTE_DIR, timeout=30)
    out.channel.recv_exit_status()

    all_ok = True
    for local, name in FILES:
        lp = os.path.join(REPO, local)
        rp = "%s/%s" % (REMOTE_DIR, name)
        t0 = __import__("time").perf_counter()
        ok, got = push(c, lp, rp)
        print("  [%s] %-22s %8d B  %.1f s  md5=%s"
              % ("OK  " if ok else "FAIL", name, os.path.getsize(lp),
                 __import__("time").perf_counter() - t0, got))
        all_ok = all_ok and ok

    c.exec_command("chmod 644 %s/*" % REMOTE_DIR)
    c.close()
    print("全部对上" if all_ok else "有文件 md5 对不上")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
