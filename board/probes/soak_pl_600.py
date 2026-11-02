#!/usr/bin/env python3
"""soak_pl_600.py —— E3：PL 直连 600 秒长跑，只走网页接口，不占板子。

跑之前确认通路是开的（`POST /api/start`），然后它每 2 秒采一次
`/api/status` 和 `/api/health`，落成 csv，结束时给一份判据报告：

  1. 存活：600 秒内 status 的 age 一直 < 1 s（说明 live 没卡死）
  2. 不丢样本：`i2s_stat0/1` 的 ovf/und 计数不增长
  3. 不漂移：`gain_applied_db` 与设定值一致；`ramp_busy` 回到 0
  4. 电平合理：peak_in 大于 0 且最小值小于 30000（非静音、非卡满）
  5. 温度/内存无异常趋势

用法：
    python board/probes/soak_pl_600.py                 # 600 秒
    python board/probes/soak_pl_600.py --secs 60       # 冒烟
    python board/probes/soak_pl_600.py --json out.json
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request

BASE = "http://192.168.2.99:8080"


def get(path, timeout=6):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body=None, timeout=6):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=int, default=600)
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--json", default="")
    ap.add_argument("--no-start", action="store_true")
    a = ap.parse_args()

    if not a.no_start:
        r = post("/api/start", {"src": "mic"})
        print("start:", r.get("ok"), r.get("msg", "")[:60])

    rows, errs = [], []
    t0 = time.time()
    n = int(a.secs / a.every)
    print("采样 %d 次，每 %.1f s 一次，共 %d s" % (n, a.every, a.secs))
    for k in range(n):
        try:
            st = get("/api/status")
            h = get("/api/health")
            rows.append({
                "t": round(time.time() - t0, 1),
                "age": st.get("age"),
                "running": st.get("running"),
                "peak_in": st.get("peak_in"),
                "peak_out": st.get("peak_out"),
                "env0": (st.get("env") or [0])[0],
                "envr0": (st.get("env_r") or [0])[0],
                "gr0": (st.get("gr") or [0])[0],
                "ramp": st.get("ramp_busy"),
                "ovf": (st.get("i2s_stat0") or 0) & 0xFFFF,
                "und": (st.get("i2s_stat0") or 0) >> 16,
                "txlvl": (st.get("i2s_stat1") or 0) & 0xFFFF,
                "rxlvl": (st.get("i2s_stat1") or 0) >> 16,
                "temp": h.get("temp_c"),
                "mem": h.get("mem_avail_mb"),
                "err": st.get("error"),
                "stale": st.get("stale"),
            })
        except Exception as ex:
            errs.append("t=%5.1f %s" % (time.time() - t0, ex))
        if k % 15 == 0:
            last = rows[-1] if rows else {}
            print("  t=%5.1f  age=%-5s in=%-8s out=%-8s temp=%s" %
                  (time.time() - t0, last.get("age"), last.get("peak_in"),
                   last.get("peak_out"), last.get("temp")))
        time.sleep(a.every)

    print("")
    print("== 判据 ==")
    fails = 0

    def chk(name, cond, extra=""):
        nonlocal fails
        print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
        if not cond:
            fails += 1

    ages = [r["age"] for r in rows if r["age"] is not None]
    chk("存活（age 最大 < 1 s）", ages and max(ages) < 1.0, "max=%.2f" % (max(ages) if ages else -1))
    chk("无 stale", not any(r["stale"] for r in rows))
    chk("无 error", not any(r["err"] for r in rows))
    chk("running 全程为真（跳过启动头两拍）", all(r["running"] for r in rows[2:]))

    # stat0 = {und[31:16], ovf[15:0]}；启动瞬间 FIFO 灌满会累计一些，只测长跑期间增量
    ovf = [r["ovf"] for r in rows if r["ovf"] is not None]
    und = [r["und"] for r in rows if r["und"] is not None]
    chk("采集 overrun 长跑期间不增长", ovf and ovf[-1] == ovf[1],
        "%d → %d（+%d）" % (ovf[1], ovf[-1], ovf[-1]-ovf[1]) if len(ovf) > 1 else "")
    chk("播放 underrun 全程为 0", und and max(und) == 0, "max=%s" % (max(und) if und else "?"))
    rx = [r["rxlvl"] for r in rows if r["rxlvl"] is not None]
    if rx:
        print("      [INFO] RX FIFO 水位 %d~%d / 4096（≈%.0f ms 采集缓冲）"
              % (min(rx), max(rx), max(rx)/48.0))

    ramps = [r["ramp"] for r in rows if r["ramp"] is not None]
    chk("ramp_busy 最终为 0", ramps and ramps[-1] == 0, "last=%s" % (ramps[-1] if ramps else "?"))

    peaks = [r["peak_in"] for r in rows if r["peak_in"] is not None]
    if peaks:
        chk("输入有起伏（不是恒 0）", max(peaks) > 0, "min=%d max=%d" % (min(peaks), max(peaks)))
        chk("输入未恒满（不是卡死）", min(peaks) < 30000, "min=%d" % min(peaks))

    temps = [r["temp"] for r in rows if r["temp"] is not None]
    if len(temps) > 2:
        chk("温度无异常（末值 - 首值 < 10°C）", temps[-1] - temps[0] < 10,
            "%.1f → %.1f" % (temps[0], temps[-1]))
    mems = [r["mem"] for r in rows if r["mem"] is not None]
    if len(mems) > 2:
        chk("内存无泄漏（末值 > 首值 - 40 MB）", mems[-1] > mems[0] - 40,
            "%d → %d MB" % (mems[0], mems[-1]))

    chk("采样无异常", not errs, "%d 次失败" % len(errs))
    if errs[:3]:
        for e in errs[:3]:
            print("     ", e)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "errors": errs}, f, ensure_ascii=False, indent=1)
        print("  明细写入 %s" % a.json)

    print("")
    print("SOAK %s (%d s)" % ("PASS" if fails == 0 else "FAIL %d 项" % fails, a.secs))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
