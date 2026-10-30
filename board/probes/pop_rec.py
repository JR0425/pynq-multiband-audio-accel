"""连续抓窗口找单点咔哒（不冻结、只取稳定段）。

**为什么不能「冻-读-解冻」**：scope_snap=1 只是停抓取，音频照跑。
一停一开，中间那些采样就没写进缓冲，缓冲里留一道相位跳变。
2026-10-31 凌晨量到：连抓 4 秒后每隔 ~86 个字一道跳变、位置固定在缓冲地址上，
全是这么造出来的。要读干净窗口，必须让抓取先自由跑满一整圈（21 ms）再冻 ——
那样 21 ms 里只能拿到 21 ms 的一半时间。

**改成**：不冻结，整块连读两次。
  抓取一直在写，写指针扫过的那一块两次读不一样 —— 那一片丢掉；
  剩下的字两次读完全相同，它们是写指针上一圈连续写下的样点，中间没有接缝。
  取其中最长的一段连续稳定区（约 700 点 = 14 ms）当窗口。
  环形只有 1024 字，指针 6 ms 走 ~290 字，所以稳定区足够长。

判据用八阶差分 max|Δ⁸x|：带宽内的平滑信号 Δ⁸ 很小（4 kHz 满幅正弦 ≈ 62），
单点跳变被放大 C(8,4)=70 倍。

    sudo /usr/local/share/pynq-venv/bin/python3 pop_rec.py -t 180
"""
import argparse
import json
import mmap
import os
import time

import numpy as np

BASE = 0x43C40000
OFF_PEAK_IN = 0x40
OFF_PEAK_OUT = 0x44
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C
N = 1024
OUT_DIR = "/home/xilinx/dma_exp"


def open_regs():
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, 4096, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)
    return fd, m, memoryview(m).cast("I")


def read_raw(mv):
    w = [0] * N
    for i in range(N):
        mv[OFF_CAP_IDX >> 2] = (i * 4) & 0xFFF
        w[i] = mv[OFF_CAP_DATA >> 2]
    return w


def longest_stable(a, b):
    """两次读相同的最长连续区，返回 (起, 长)。"""
    best_s, best_l = 0, 0
    s = None
    for i in range(N + 1):
        ok = i < N and a[i] == b[i]
        if ok and s is None:
            s = i
        elif not ok and s is not None:
            if i - s > best_l:
                best_s, best_l = s, i - s
            s = None
    return best_s, best_l


def split(words):
    a = np.array(words, dtype=np.uint32)
    ins = ((a & 0xFFFF).astype(np.int32) << 16) >> 16
    outs = ((a >> 16).astype(np.int32) << 16) >> 16
    return ins, outs


def d8(x):
    return np.abs(np.diff(x, n=8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--secs", type=float, default=180.0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--cond", type=float, default=1500.0,
                    help="Δ⁸ 超过它就存窗口（int16 单位）")
    ap.add_argument("--minlen", type=int, default=256)
    a = ap.parse_args()

    fd, m, mv = open_regs()
    stamp = time.strftime("%H%M%S")
    csv_path = os.path.join(OUT_DIR, "poprec_%s%s.csv" % (a.tag, stamp))
    f = open(csv_path, "w", buffering=1)
    f.write("t,pin,pout,len,d8out,d8in,rmsout,imax_o,imax_i\n")

    t0 = time.time()
    n_read = 0
    n_ok = 0
    n_flag = 0
    len_hist = []
    best = []
    nxt = t0 + 10.0

    print("录 %ss（%s），判据 Δ⁸ > %.0f" % (a.secs, a.tag, a.cond), flush=True)
    while time.time() - t0 < a.secs:
        w1 = read_raw(mv)
        w2 = read_raw(mv)
        n_read += 1
        s, ln = longest_stable(w1, w2)
        if ln < a.minlen:
            continue
        n_ok += 1
        len_hist.append(ln)
        ins, outs = split(w2[s:s + ln])
        pin = mv[OFF_PEAK_IN >> 2] & 0xFFFF
        pout = mv[OFF_PEAK_OUT >> 2] & 0xFFFF
        do = d8(outs)
        di = d8(ins)
        mo, mi = int(do.max()), int(di.max())
        t = time.time() - t0
        rms = float(np.sqrt(np.mean(outs.astype(np.float64) ** 2)))
        f.write("%.3f,%d,%d,%d,%d,%d,%.1f,%d,%d\n"
                % (t, pin, pout, ln, mo, mi, rms, int(do.argmax()), int(di.argmax())))
        if mo > a.cond or mi > a.cond:
            n_flag += 1
            best.append((max(mo, mi), t, ins.tolist(), outs.tolist(), pin, pout, mo, mi))
            best.sort(key=lambda w: -w[0])
            del best[8:]
            print("  ! t=%7.3fs  长%4d  Δ⁸out=%6d Δ⁸in=%6d  rms=%.0f  pin=%d pout=%d"
                  % (t, ln, mo, mi, rms, pin, pout), flush=True)
        if time.time() >= nxt:
            nxt += 20.0
            print("  %6.1fs  %d 次读 / %d 段可用  中位长 %d  %d 可疑  mo=%d mi=%d"
                  % (time.time() - t0, n_read, n_ok,
                     int(sorted(len_hist)[len(len_hist) // 2]) if len_hist else 0,
                     n_flag, mo, mi), flush=True)

    f.close()
    for i, (mx, t, ins, outs, pin, pout, mo, mi) in enumerate(best):
        p = os.path.join(OUT_DIR, "popr_%s%s_%d.json" % (a.tag, stamp, i))
        with open(p, "w") as g:
            json.dump({"t": t, "d8out": mo, "d8in": mi,
                       "peak_in": pin, "peak_out": pout,
                       "in": ins, "out": outs}, g)
    print("结束：%d 次读，%d 段可用，%d 可疑；CSV %s；top %d 窗口 popr_%s%s_*.json"
          % (n_read, n_ok, n_flag, csv_path, len(best), a.tag, stamp))
    del mv


if __name__ == "__main__":
    main()
