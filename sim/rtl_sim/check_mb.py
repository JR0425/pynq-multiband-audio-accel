#!/usr/bin/env python3
"""把 fir_mb_fe 的仿真输出和 sim/model/ref_model_mb.py 的参考逐位比对。

  用法：python sim/rtl_sim/check_mb.py

比 9 路，全部逐位：
    rtl_fe_ylp0/1/2.mem   vs  ref_fe_ylp0/1/2.mem    三个低通累加器（40 位）
    rtl_fe_band0..3.mem   vs  ref_fe_band0..3.mem    四段（40 位）
    rtl_fe_sum.mem        vs  ref_fe_sum.mem         四段之和（40 位）
    rtl_fe_out.txt        vs  ref_fe_out.txt         最终输出（int16）

为什么把中间量全比一遍：
    输出只占 16 位，低 17 位全被右移扔掉了。如果只比输出，
    "b1 里减错一个低通"和"移位量写错"在文件里长得一样。
    先比累加器能定位到组、再比段能定位到那一减法、最后比总和能把
    "四段各自对但综合错"这一类单独拎出来。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# (RTL 文件, 参考文件, 是否十六进制, 说明)
STREAMS = [
    ("rtl_fe_ylp0.mem", "ref_fe_ylp0.mem", True,  "低通 LP500"),
    ("rtl_fe_ylp1.mem", "ref_fe_ylp1.mem", True,  "低通 LP1000"),
    ("rtl_fe_ylp2.mem", "ref_fe_ylp2.mem", True,  "低通 LP2000"),
    ("rtl_fe_band0.mem", "ref_fe_band0.mem", True, "段 0 = y0"),
    ("rtl_fe_band1.mem", "ref_fe_band1.mem", True, "段 1 = y1-y0"),
    ("rtl_fe_band2.mem", "ref_fe_band2.mem", True, "段 2 = y2-y1"),
    ("rtl_fe_band3.mem", "ref_fe_band3.mem", True, "段 3 = x[96]-y2"),
    ("rtl_fe_sum.mem",  "ref_fe_sum.mem",  True,  "四段之和"),
    ("rtl_fe_out.txt",  "ref_fe_out.txt",  False, "最终输出 int16"),
]


def rd(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]


def s40(v):
    """40 位补码转成有符号，只为了打印差值好看。"""
    return v - (1 << 40) if v >= (1 << 39) else v


def cmp_stream(rtl_name, ref_name, is_hex, label):
    rp = os.path.join(HERE, rtl_name)
    fp = os.path.join(HERE, ref_name)
    if not os.path.exists(rp) or not os.path.exists(fp):
        print(f"  {label:18s}: 缺文件 "
              f"{os.path.basename(rp if not os.path.exists(rp) else fp)}")
        return False

    ra, fa = rd(rp), rd(fp)
    if len(ra) != len(fa):
        print(f"  {label:18s}: 个数 {len(ra)} vs 参考 {len(fa)} -> 长度先不对，"
              f"查 dout_vld 对齐")
        return False

    if is_hex:
        va = [int(s, 16) for s in ra]
        vf = [int(s, 16) for s in fa]
    else:
        va = [int(s) for s in ra]
        vf = [int(s) for s in fa]

    bad = [i for i, (a, b) in enumerate(zip(va, vf)) if a != b]
    ok = not bad
    print(f"  {label:18s}: [{'OK ' if ok else 'FAIL'}] "
          f"{len(va) - len(bad)}/{len(va)} 位相同", end="")
    if ok:
        print()
    else:
        i = bad[0]
        if is_hex:
            print(f"   <-- 首个不符 @{i}: rtl=0x{ra[i]} ref=0x{fa[i]} "
                  f"差 {s40(va[i]) - s40(vf[i])}（共 {len(bad)} 处）")
        else:
            print(f"   <-- 首个不符 @{i}: rtl={ra[i]} ref={fa[i]} "
                  f"差 {va[i] - vf[i]}（共 {len(bad)} 处）")
    return ok


def main():
    print("### 逐位比对（9 路）")
    all_ok = True
    for name, ref, is_hex, label in STREAMS:
        if not cmp_stream(name, ref, is_hex, label):
            all_ok = False

    print()
    if all_ok:
        print("9 路全部逐位相同。")
        print("这只证明 RTL 和整数模型一致；模型本身对不对，看 ref_model_mb.py")
        print("打印的三条自检（Σband==x[96]<<17、out==延迟输入、和 HLS csim 逐位比）。")
        return 0
    print("有差异。定位顺序：")
    print("  · 某一路低通就不对      -> 乘法 / 加法树 / 抽头下标 / 该组系数没切到")
    print("  · 低通对、某一段不对    -> 只剩那一减法")
    print("  · 四段都对、总和不对    -> 综合那一级")
    print("  · 总和对、输出不对      -> 右移量 / 饱和")
    return 1


if __name__ == "__main__":
    sys.exit(main())
