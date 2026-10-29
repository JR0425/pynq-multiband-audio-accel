#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_rec.py —— 录一段实时说话的处理前后对比。**在 Windows 上跑。**

    python board/probes/demo_rec.py 15

做的事：让板子用演示档位（L=360/NBUF=2，嘴到耳 16.2 ms）开实时，
对着耳机麦说 N 秒，然后停下、等自检跑完、把两个 wav 拉回电脑：

    data/audio/demo/take_in.wav     处理前（进核的信号）
    data/audio/demo/take_out.wav    处理后（出核的信号）

两个文件是**同一趟、逐块对齐**的，对着放即可对比处理前后的差别。

为什么走网页服务而不是直接起 fir_live：
  fir_live.py 每次只允许一条在跑（有文件锁）。从网页服务起，现场那一趟
  就是"点网页上的启动"起的那一条，录下来的东西和台上听到的是同一条通路。

⚠️ 这个脚本会**覆盖板子上的** fir_live.py / fir_webui.py，并重启 fir-webui。
"""

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"
BOARD_DIR = "/home/xilinx/jupyter_notebooks"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, "data", "audio", "demo")
REC_PREFIX = "/tmp/take"          # 只准 /tmp 或 /dev/shm（服务端会检查）
BASE = "http://%s:8080" % HOST


def ssh():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD, timeout=25,
              banner_timeout=25, auth_timeout=25)
    return c


def run(c, cmd, timeout=120):
    _in, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    return out.channel.recv_exit_status(), o, e


def push(c, local, remote):
    """base64 直灌 + 在**板上用 md5sum 核对**。

    2026-10-23 用 sftp 写过一次：返回成功、读回来的 md5 也对，但板上实际没变
    （fir_webui.py 停在 55694 字节的旧版，整轮"推成功"是假的）。
    SFTP 那条路的回读不可信，改成和 perf_probe.py 一样的做法：把内容灌进
    `base64 -d`，再让板子自己算 md5。
    """
    with open(local, "rb") as f:
        raw = f.read().replace(b"\r\n", b"\n")
    want = hashlib.md5(raw).hexdigest()
    _in, out, err = c.exec_command("base64 -d > %s" % remote, timeout=180)
    _in.write(base64.b64encode(raw))
    _in.channel.shutdown_write()
    rc = out.channel.recv_exit_status()
    if rc != 0:
        raise SystemExit("写 %s 失败：%s" % (remote, err.read().decode("utf-8", "replace")))
    rc, o, e = run(c, "md5sum " + remote)
    got = o.split()[0] if o.strip() else ""
    if got != want:
        raise SystemExit("push 校验失败：%s 期望 %s 实际 %s" % (remote, want, got))


def http(path, body=None, timeout=15):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_service(sec=20):
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            http("/api/status", timeout=3)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def status():
    """返回 /api/status 里**内层**那一份（running / n / in_rms / ok 都在这里）。

    外层还有个 {"proc": ..., "last": ...}，直接拿外层的话 running 永远是 None，
    循环会以为"已经停了"立刻跳出去。
    """
    try:
        return http("/api/status", timeout=5).get("status") or {}
    except Exception:                                     # noqa: BLE001
        return {}


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法：python board/probes/demo_rec.py <秒数>")
    secs = int(sys.argv[1])
    if not 3 <= secs <= 280:
        raise SystemExit("秒数给 3~280（内存里只留这么长）")

    os.makedirs(OUT_DIR, exist_ok=True)
    c = ssh()
    try:
        print("推板上的两个脚本…", flush=True)
        for name in ("fir_live.py", "fir_webui.py"):
            push(c, os.path.join(REPO, "board", "scripts", name),
                 os.path.join(BOARD_DIR, name))
            print("   ok", name, flush=True)

        print("重启 fir-webui…", flush=True)
        run(c, "echo xilinx | sudo -S systemctl restart fir-webui", timeout=60)
        if not wait_service(30):
            raise SystemExit("网页服务没起来，先看 systemctl status fir-webui")

        print("停掉可能在跑的那一条…", flush=True)
        http("/api/stop", {})
        t0 = time.time()
        while time.time() - t0 < 40:
            st = status()
            if not st.get("running"):
                break
            time.sleep(1)

        run(c, "rm -f %s_in.wav %s_out.wav" % (REC_PREFIX, REC_PREFIX))

        print("\n=== 等板子跑起来（开 overlay + import，约 7 秒）===", flush=True)
        r = http("/api/start", {"src": "mic", "l": 360, "nbuf": 2,
                                "rec": REC_PREFIX, "cap_sec": max(20, secs)})
        if not r.get("ok"):
            raise SystemExit("起不来：%s" % r.get("msg"))

        t0 = time.time()
        while time.time() - t0 < 60:
            st = status()
            if st.get("running") and (st.get("n") or 0) > 0:
                break
            time.sleep(0.5)
        else:
            raise SystemExit("60 秒还没出第一块，先看 /api/status 和板上的 log")

        print("\n=== 开始录 %d 秒，现在就说话 ===" % secs, flush=True)
        t0 = time.time()
        while time.time() - t0 < secs:
            left = secs - (time.time() - t0)
            st = status()
            level = st.get("in_rms")
            print("   还剩 %.0f 秒%s" % (
                left, ("   进核 rms %4.0f" % level) if level else ""), flush=True)
            time.sleep(2)
        print("\n停，等自检和写盘…", flush=True)
        http("/api/stop", {})

        t0 = time.time()
        ok = None
        while time.time() - t0 < 120:
            st = status()
            if not st.get("running"):
                ok = st.get("ok")
                break
            time.sleep(1)
        print("   自检结论：%s" % ({True: "逐位相同", False: "对不上", None: "未拿到"}[ok]),
              flush=True)

        rc, o, e = run(c, "ls -l %s_in.wav %s_out.wav" % (REC_PREFIX, REC_PREFIX))
        if rc != 0:
            raise SystemExit("板子上没生成 wav：%s" % (e or o).strip())
        print(o.strip(), flush=True)

        sftp = c.open_sftp()
        try:
            dests = []
            for suffix in ("_in", "_out"):
                local = os.path.join(OUT_DIR, "take%s.wav" % suffix)
                sftp.get(REC_PREFIX + suffix + ".wav", local)
                dests.append(local)
        finally:
            sftp.close()

        for p in dests:
            sz = os.path.getsize(p)
            print("   %s  %.1f KB  %.2f 秒"
                  % (p, sz / 1024.0, max(0, sz - 44) / 2.0 / 48000.0), flush=True)
        print("\n处理前 = take_in.wav，处理后 = take_out.wav。"
              "\n同一趟、逐块对齐，对着放就能听出差别。", flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    main()
