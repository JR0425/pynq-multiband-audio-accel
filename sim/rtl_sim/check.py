#!/usr/bin/env python3
"""把 RTL 仿真吐出来的文件，和 tools/ref_model_int.py 的参考模型逐位比对。

  用法：python sim/rtl_sim/check.py [--taps 193] [--edges 500,1000,2000]

比两样东西，两样都是**逐位**：
    rtl_acc_lp<E>.mem  vs  ref_acc_lp<E>.mem    累加器原值（40 位）
    rtl_out_lp<E>.txt  vs  ref_out_lp<E>.txt    最终输出（int16）

为什么先比累加器、再比输出：
    输出是"右移 17 + 饱和"之后的结果，会把低 17 位的信息全部扔掉。
    如果只比输出，一个"乘法算错了一点点"和"移位量写错了"会长得一模一样。
    先比累加器，错在哪一步就是确定的：
        · 累加器就不对     -> 乘法 / 加法树 / 抽头下标 的事
        · 累加器对、输出不对 -> 只剩移位量和饱和这两行
    这也是为什么 RTL 把 acc_raw 单独引出来 —— 它不是给板子用的，是给这一步用的。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def rd(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193)
    ap.add_argument("--edges", default="500,1000,2000")
    args = ap.parse_args()
    edges = [int(e) for e in args.edges.split(",")]

    rc = 0
    for e in edges:
        acc_r = os.path.join(HERE, f"rtl_acc_lp{e}.mem")
        acc_f = os.path.join(HERE, f"ref_acc_lp{e}.mem")
        out_r = os.path.join(HERE, f"rtl_out_lp{e}.txt")
        out_f = os.path.join(HERE, f"ref_out_lp{e}.txt")

        missing = [p for p in (acc_r, acc_f, out_r, out_f) if not os.path.exists(p)]
        if missing:
            print(f"  LP{e:5d}: 缺文件 {[os.path.basename(m) for m in missing]}")
            rc = 1
            continue

        ra, fa = rd(acc_r), rd(acc_f)
        ro, fo = rd(out_r), rd(out_f)

        if len(ra) != len(fa):
            print(f"  LP{e:5d}: 累加器个数 {len(ra)} vs 参考 {len(fa)}  -> 长度就对不上，"
                  f"先查 dout_vld 的对齐和 LEN")
            rc = 1
            continue

        bad_acc = [i for i, (a, b) in enumerate(zip(ra, fa))
                   if int(a, 16) != int(b, 16)]
        bad_out = [i for i, (a, b) in enumerate(zip(ro, fo)) if int(a) != int(b)]

        tag = "OK " if (not bad_acc and not bad_out) else "FAIL"
        print(f"  LP{e:5d}: [{tag}] 累加器 {len(ra) - len(bad_acc)}/{len(ra)} 位相同"
              f"，输出 {len(ro) - len(bad_out)}/{len(ro)} 位相同", end="")
        if bad_acc:
            i = bad_acc[0]
            print(f"   <-- 首个累加器不符 @{i}: rtl={ra[i]} ref={fa[i]}"
                  f" 差 {int(ra[i], 16) - int(fa[i], 16)}")
        else:
            print()
        if bad_out and not bad_acc:
            i = bad_out[0]
            print(f"       累计器全对但输出有 {len(bad_out)} 处不符，"
                  f"首个 @{i}: rtl={ro[i]} ref={fo[i]}  -> 只剩移位/饱和这两行要查")
        if bad_acc or bad_out:
            rc = 1

    print()
    if rc == 0:
        print("RTL 输出和整数参考模型**逐位相同**。")
        print("注意这只说明 RTL 和这个模型一致；模型本身对不对，看 ref_model_int.py")
        print("打印的 SNR（和浮点基线比）—— 那一步已经跑过了。")
    else:
        print("有差异，见上面。累加器不符先查乘法和抽头下标；")
        print("累加器全对而输出不符，只需要看右移量和饱和那两行。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
