"""§3.4.1 的 A/B 素材：在板上跑一趟，把进核/出核两个 wav 取回来。

走的是网页服务自己的 HTTP 接口（和点界面上的按钮同一条路），不 SSH：
    POST /api/tune   把参数设成设定 A[0]（thr 0.030 / ratio 0.5 / 5 ms / 100 ms）
    POST /api/start  src=wav，放 board_input_48k.wav，开录音，录满 6 秒
    POST /api/stop   停，等收尾自检
    GET  /api/rec    取 take_in.wav / take_out.wav

这两个 wav 就是逐位自检用的那一对（fir_live.py 的 cap_in / cap_out），
不是另开一路采集，所以打开录音对实时通路零影响。

跑完把参数还原成原来那份（/dev/shm/fir_tune.json 是板上的临时状态）。
"""

import json
import os
import sys
import time
import urllib.request

D = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("BOARD", "http://192.168.2.99:8080")
OUTDIR = os.path.join(D, "data", "audio")

SET4 = {"gain_db": [0.0] * 4, "thr_db": [-30.45] * 4, "ratio": [0.5] * 4,
        "attack_ms": [5.0] * 4, "release_ms": [100.0] * 4, "bypass": 0}


def req(path, body=None, timeout=30):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.urlopen(urllib.request.Request(BASE + path, data, headers),
                               timeout=timeout)
    return r.read()


def jreq(path, body=None, timeout=30):
    return json.loads(req(path, body, timeout).decode("utf-8"))


def wait(cond, timeout, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return False


def status():
    """起跑的头几秒 /api/status 里 status 还是 null，这里抹平成空字典。"""
    d = jreq("/api/status")
    return d.get("status") or {}, d.get("proc") or {}


def main():
    orig = jreq("/api/tune")["tune"]
    print("原参数：", json.dumps(orig, ensure_ascii=False))

    try:
        print("设成 A[0]：", json.dumps(SET4, ensure_ascii=False))
        print(" ->", jreq("/api/tune", SET4))

        s, p = status()
        if s.get("running") or p.get("running"):
            raise SystemExit("板上已经有一条在跑，先停掉再执行")

        body = {"src": "wav", "wav": "board_input_48k.wav", "rec": "/tmp/take",
                "cap_sec": 6, "l": 480, "nbuf": 3}
        print("起跑：", body)
        print(" ->", jreq("/api/start", body, timeout=60))

        if not wait(lambda: status()[0].get("n", 0) > 0, 60):
            raise SystemExit("60 秒内没有跑起来")
        print("跑起来了，等录满 6 秒")
        time.sleep(9.0)

        print("停：", jreq("/api/stop", {}, timeout=60))
        if not wait(lambda: not status()[1].get("running"), 180):
            raise SystemExit("停了 180 秒还没收尾")

        s, p = status()
        print("收工：%s" % p.get("stop_msg"))
        print("      块数 %d，丢块 %s，实时率 %.3f，缓冲 %.1f ms"
              % (s.get("n", 0), s.get("lost_blocks"), s.get("rt", 0),
                 s.get("lat_ms", 0)))

        for name in ("take_in.wav", "take_out.wav"):
            blob = req("/api/rec?name=" + name, timeout=60)
            dst = os.path.join(OUTDIR, "ab_board_" + name.replace("take_", ""))
            with open(dst, "wb") as fh:
                fh.write(blob)
            print("取回 %s  %d 字节 -> %s" % (name, len(blob), dst))

        with open(os.path.join(D, "data", "results", "ab_board_run_20261024.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"params": SET4, "start": body, "stop_msg": p.get("stop_msg"),
                       "status": s}, fh, ensure_ascii=False, indent=2)
    finally:
        # 参数是一直播的：跑着的时候改回去，正在跑的这一趟会中途换参数。
        # 所以先确保停了，再还原。
        try:
            if status()[1].get("running"):
                jreq("/api/stop", {}, timeout=60)
                wait(lambda: not status()[1].get("running"), 180)
        except Exception as e:                              # noqa: BLE001
            print("收尾停进程失败：%r" % (e,))
        print("还原参数：", jreq("/api/tune", orig))


if __name__ == "__main__":
    sys.exit(main())
