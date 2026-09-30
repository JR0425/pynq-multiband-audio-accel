"""把低通系数转成 C 头文件，供 HLS 核编译时用。

为什么要有这一步：
    193 个抽头 × 3 组 = 579 个浮点数，手抄进 C 代码必错，而且以后
    改边界/改抽头数时两边会不一致。所以走自动生成：
        band_design.py  ->  本脚本  ->  src/hls/fir_coeffs_n<taps>.h

    每个抽头数一份文件，不共用同一个名字 —— 两组的系数完全不同，
    共用名字的话"上一次生成的是哪个"会变成一个靠记忆的问题，
    而综合报告里写的抽头数必须能对上实际编译的那份。

用法（在仓库根目录）：
    python src/python/export_coeffs_header.py            # 默认 193，已定的设计点
    python src/python/export_coeffs_header.py --taps 65  # 对照版
    两个都生成：
    python src/python/export_coeffs_header.py --all
"""

import argparse
import os
import subprocess
import sys

import numpy as np

from band_design import BAND_EDGES, N_BANDS, N_LP, group_delay

TXT_FMT = "sim/hls_csim/lp_{}_n{}.txt"
DST_FMT = "src/hls/fir_coeffs_n{}.h"

# 系数表按这个数补齐（多出来的系数写 0）。
#
# 为什么补：内核把抽头分成 FIR_PARTIAL 组并行累加（"k 每轮跳 FIR_PARTIAL 个"），
# 这要求 N_TAPS 能被 FIR_PARTIAL 整除。65 可以（=5×13），**193 不行**（193 是质数）。
# 想绕开的话得在循环里加 `if (k+p < N_TAPS)`，但那会让展开后的下标
# （k+p 最大到 194）在静态检查时越出 193 个元素的数组，HLS 前端未必肯放过。
# 直接在表的尾巴上补零最省事：多出来的项乘的是 0，数值一个字不动，
# 硬件上也就多两个乘加（579 个里的 2 个）。
PAD_QUANTUM = 5

TEMPLATE = """/* 自动生成，请勿手改 —— 改这个文件没用，重新生成会覆盖掉。
 *
 * 生成命令：python src/python/export_coeffs_header.py --taps {n}
 * 上游：sim/hls_csim/lp_{{edge}}_n{n}.txt（由 src/python/export_coefficients.py 导出）
 *       频段划分的定义在 src/python/band_design.py
 *
 * 设计采样率 48000 Hz —— 与板载 ADAU1761 的实际采样率一致（RTL 里写死）。
 * 抽头数 {n}，群延迟 D = {d} 采样。
 *
 * 只有 {nlp} 组**低通**，不是 {nbands} 组频段 —— 频段是当场相减出来的：
 *     b1 = y1                b2 = y2 - y1
 *     b3 = y3 - y2           b4 = hist[D] - y3
 * 各段之和恒等于延迟 D 拍的输入。推导见 src/hls/fir_multiband.cpp 文件头。
 *
 * ⚠️ 表的类型是 coef_t，不是 float —— 由 fir_types.h 按编译开关定：
 *     浮点模式  coef_t = float
 *     定点模式  coef_t = ap_fixed<FIR_CW,1>
 *   表写成 coef_t 是**故意的，不是随手**：实测过写成 const float、在循环里再转定点，
 *   HLS 不会在综合时折掉那个转换，而是生成 65 个 float→double 扩位器，
 *   系数表也从 8 块 BRAM 涨到 128 块，LUT 炸到 232% 装不下。
 *   表本身就是定点的，硬件里就一次转换都不用做。
 */

#ifndef FIR_COEFFS_N{n}_H
#define FIR_COEFFS_N{n}_H

#include "fir_types.h"

#define N_TAPS       {n}
#define N_TAPS_ALLOC {alloc}   /* 补齐后的长度：尾部的系数是 0，见下 */
#define N_BANDS      {nbands}
#define N_LP         {nlp}

/* 段边界（Hz）。只作注释/调试用，硬件里不参与运算 ——
 * 运算需要的是系数本身，不是截止频率（现场算 sin/cos 加窗贵得多且没必要）。 */
#define BAND_EDGE_1 {e1}
#define BAND_EDGE_2 {e2}
#define BAND_EDGE_3 {e3}

/* {nlp} 组低通，按边界从低到高。严格对称（线性相位），各段延迟因此完全一致。
 * 表长是 N_TAPS_ALLOC，最后 {pad} 个是补的 0 —— 让内核那个
 * "k 每轮跳 FIR_PARTIAL 个"的分组不用处理尾巴。0 乘任何数都是 0，数值不受影响。 */
static const coef_t FIR_LP[N_LP][N_TAPS_ALLOC] = {{
{body}
}};

#endif /* FIR_COEFFS_N{n}_H */
"""


def build(n):
    alloc = ((n + PAD_QUANTUM - 1) // PAD_QUANTUM) * PAD_QUANTUM
    rows = []
    maxabs = 0.0
    for edge in BAND_EDGES:
        path = TXT_FMT.format(edge, n)
        if not os.path.exists(path):
            raise SystemExit(f"找不到 {path}\n先跑：python src/python/export_coefficients.py --taps {n}")
        taps = np.loadtxt(path)
        if len(taps) != n:
            raise SystemExit(f"{path} 里是 {len(taps)} 个系数，期望 {n}")
        if np.max(np.abs(taps - taps[::-1])) > 1e-12:
            raise SystemExit(f"{path} 的系数不对称 —— 相减式要求各段严格等延迟")
        maxabs = max(maxabs, float(np.max(np.abs(taps))))
        padded = np.concatenate([taps, np.zeros(alloc - n)])
        vals = ", ".join(f"{v:+.9e}f" for v in padded)
        rows.append(f"    {{ {vals} }},   /* 低通 {edge} Hz */")

    text = TEMPLATE.format(
        n=n, alloc=alloc, pad=alloc - n, d=group_delay(n),
        nbands=N_BANDS, nlp=N_LP,
        e1=BAND_EDGES[0], e2=BAND_EDGES[1], e3=BAND_EDGES[2],
        body="\n".join(rows),
    )
    dst = DST_FMT.format(n)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"已生成 {dst}（{N_LP} 组 × {n} 个系数，补齐到 {alloc}；max|h| = {maxabs:.6f}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193,
                    help="默认 193（已定的设计点）；--all 把 65 和 193 都生成")
    ap.add_argument("--all", action="store_true", help="生成 65 和 193 两份")
    args = ap.parse_args()

    # 系数文件缺失时现生成一份，免得"忘了先跑上一步"变成一个看不懂的报错
    taps_list = [65, 193] if args.all else [args.taps]
    for n in taps_list:
        subprocess.run([sys.executable, "src/python/export_coefficients.py", "--taps", str(n)],
                       check=True)
        build(n)


if __name__ == "__main__":
    main()
