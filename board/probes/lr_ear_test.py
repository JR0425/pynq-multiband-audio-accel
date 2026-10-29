"""lr_ear_test —— 用两只耳朵判「左右两套参数是不是真的各走各的」。

板子已经在跑 fir_pl_live + fir_pl_webui，这个脚本只走 HTTP 调音接口，
不改寄存器、不碰位流（所以不会被后台每 50 ms 的 tune->regs 覆盖）。

它把两只耳朵的增益拉开 42 dB，让"该响的那边"和"该静的那边"差到不用比：

    只有左耳  +18 dB / -24 dB
    只有右耳  -24 dB / +18 dB
    两边都响  +18 dB / +18 dB

每档 6 秒、跑 2 轮，最后恢复原调音。

回答只有三种：
    1. 三档都符合          -> 双耳通路是分开的，之前"两边一样"是别的原因
    2. 「只有左耳」两边都响 -> 左声道被复制到两个耳朵（发送/插头/codec 三选一）
    3. 该响的没响、另一边响 -> 左右反了

用法：
    python board/probes/lr_ear_test.py
    python board/probes/lr_ear_test.py --url http://192.168.2.99:8080 --secs 6
"""
import argparse
import json
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOUD, QUIET = 18.0, -24.0
THR_OPEN = 0.0          # 门限拉到 0 dBFS = 不压缩，增益差才干净


def api(base, path, body=None):
    url = base.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def shape(tune, left, right):
    t = dict(tune)
    t["gain_db"] = [left] * 4
    t["thr_db"] = [THR_OPEN] * 4
    t["bypass"] = False
    t.pop("tone", None)
    b = dict(t.get("b1") or {})
    b["gain_db"] = [right] * 4
    b["thr_db"] = [THR_OPEN] * 4
    t["b1"] = b
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.2.99:8080")
    ap.add_argument("--secs", type=float, default=6.0)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()

    try:
        orig = api(a.url, "/api/tune")["tune"]
    except Exception as e:
        print("连不上调音接口 %s：%s" % (a.url, e))
        return 1
    print("原调音：左 %s / 右 %s"
          % (orig.get("gain_db"), (orig.get("b1") or {}).get("gain_db")))

    phases = [("只有左耳该响", LOUD, QUIET),
              ("只有右耳该响", QUIET, LOUD),
              ("两边都该响", LOUD, LOUD)]
    try:
        api(a.url, "/api/start", {})
        for rnd in range(a.rounds):
            for name, l, r in phases:
                t = api(a.url, "/api/tune", shape(orig, l, r))["tune"]
                back = api(a.url, "/api/tune")["tune"]
                ok = (back["gain_db"] == t["gain_db"]
                      and back["b1"]["gain_db"] == t["b1"]["gain_db"])
                print("\n第%d轮  %-14s 左 %+5.1f dB / 右 %+5.1f dB%s"
                      % (rnd + 1, name, l, r, "" if ok else "  (回读不一致!)"))
                for k in range(int(a.secs), 0, -1):
                    print("\r  %d" % k, end="")
                    sys.stdout.flush()
                    time.sleep(1)
                print("\r  --", flush=True)
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        api(a.url, "/api/tune", orig)
        print("\n已恢复原调音")
    print("""
听下来的结论照这三种之一说：
  1. 三档都对            -> 双耳是分开的
  2. 「只有左耳」两边都响 -> 左声道串进了两只耳朵
  3. 该响的没响、另一边响 -> 左右接反了
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
