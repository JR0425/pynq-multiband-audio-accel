"""lowband_listen —— 判断底噪是不是 50 Hz 工频。

把四段里的最低段单独抬到 +24 dB、其余压到 -24 dB，压缩门限全开（不压缩），
输出限幅 -20 dBFS 护耳，听 N 秒后自动写回原处方。

判断：
    听到有音高的 "嗡——"（像变压器/日光灯） -> 是工频哼声，得从供电/屏蔽下手
    听到没音高的 "呼——"                     -> 房间低频噪声或话筒自噪声

用法：
    python board/probes/lowband_listen.py
    python board/probes/lowband_listen.py --secs 25 --url http://192.168.2.99:8080
"""
import argparse
import json
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LO, HI = 24.0, -24.0
MPO = -20.0


def api(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def shape(tune):
    t = dict(tune)
    t["gain_db"] = [LO, HI, HI, HI]
    t["thr_db"] = [0.0] * 4           # 0 dBFS = 不压缩，本底才不会被压
    t["mpo_db"] = MPO
    t.pop("tone", None)
    b = dict(t.get("b1") or {})
    b["gain_db"] = [LO, HI, HI, HI]
    b["thr_db"] = [0.0] * 4
    t["b1"] = b
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.2.99:8080")
    ap.add_argument("--secs", type=float, default=25.0)
    a = ap.parse_args()

    try:
        orig = api(a.url, "/api/tune")["tune"]
    except Exception as e:
        print("连不上调音接口 %s：%s" % (a.url, e))
        return 1

    api(a.url, "/api/start", {})
    try:
        api(a.url, "/api/tune", shape(orig))
        print("只放最低段，%d 秒后自动恢复 —— 请安静听" % int(a.secs), flush=True)
        for k in range(int(a.secs), 0, -1):
            print("\r  %d " % k, end="")
            sys.stdout.flush()
            time.sleep(1)
        print("\r  --", flush=True)
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        api(a.url, "/api/tune", orig)
        print("已恢复原调音")
    print("""
听着判断：
  有音高的 "嗡——"  -> 50 Hz 工频哼声
  没音高的 "呼——"  -> 房间低频噪声 / 话筒自噪声
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
