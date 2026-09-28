"""把 4 组 FIR 系数从纯文本转成 C 头文件，供 HLS 核编译时使用。

为什么要有这一步：
    65 个抽头 × 4 个频段 = 260 个浮点数，手抄进 C 代码必错，而且以后
    重新设计滤波器时两边会不一致。所以走自动生成：
        export_coefficients.py   -> sim/hls_csim/fir_coeffs_{1..4}.txt
        本脚本                    -> src/hls/fir_coeffs.h
    人只改滤波器的设计参数，其余全自动。

用法（在仓库根目录）：
    python src/python/export_coeffs_header.py
"""

import os
import numpy as np

N_TAPS = 65
N_BANDS = 4
SRC_FMT = "sim/hls_csim/fir_coeffs_{}.txt"
DST = "src/hls/fir_coeffs.h"


def main():
    rows = []
    for band in range(1, N_BANDS + 1):
        path = SRC_FMT.format(band)
        if not os.path.exists(path):
            raise SystemExit(f"找不到系数文件 {path}，先跑 export_coefficients.py")
        taps = np.loadtxt(path)
        if len(taps) != N_TAPS:
            raise SystemExit(f"{path} 里是 {len(taps)} 个系数，期望 {N_TAPS}")
        vals = ", ".join(f"{v:+.9e}f" for v in taps)
        rows.append(f"    {{ {vals} }}   /* 频段 {band} */")

    body = ",\n".join(rows)

    text = f"""/* 自动生成，请勿手改 —— 改这个文件没用，重新生成会覆盖掉。
 *
 * 生成命令：python src/python/export_coeffs_header.py
 * 上游：sim/hls_csim/fir_coeffs_{{1..4}}.txt（由 src/python/export_coefficients.py 导出）
 *
 * 设计采样率 48000 Hz —— 与板载 ADAU1761 的实际采样率一致（RTL 里写死）。
 * 抽头数 {N_TAPS}，4 个频段：
 *   1: 低通   0-300 Hz
 *   2: 带通 300-600 Hz
 *   3: 带通 600-1000 Hz
 *   4: 高通 1000-8000 Hz
 */

#ifndef FIR_COEFFS_H
#define FIR_COEFFS_H

#define N_TAPS {N_TAPS}
#define N_BANDS {N_BANDS}

static const float FIR_COEFFS[N_BANDS][N_TAPS] = {{
{body}
}};

#endif /* FIR_COEFFS_H */
"""
    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"已生成 {DST}（{N_BANDS} 组 × {N_TAPS} 个系数）")


if __name__ == "__main__":
    main()
