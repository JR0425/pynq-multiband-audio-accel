# HLS C 仿真素材

这个目录放 C 仿真要用的**系数表**。跑仿真的脚本在 `build/hls/run_csim.tcl`，
核和测试台在 `src/hls/`，都不在这个目录下 —— 别在这儿找。

## 文件说明

| 文件 | 用途 |
| :--- | :--- |
| `lp_500_n193.txt` 等 6 个 | **低通**系数，不是"每段一个带通"。见下面"为什么只有低通"。 |
| `test_fir.cpp` | 早期那份独立的浮点 FIR demo，**已经被取代**，不参与现在的流程。留着只是存档。 |

系数文件命名是 `lp_<截止频率>_n<抽头数>.txt`，一行一个数，十进制浮点。
抽头数和硬件固件里那个 `FIR_N_TAPS` 必须对上。

## 为什么只有低通，没有"频段 1/2/3/4"

频段划分是**相减式**：

```
b1 = LP500
b2 = LP1000 - LP500
b3 = LP2000 - LP1000
b4 = 输入延迟 D 拍 - LP2000        D = (抽头数-1)/2
```

所以只要 3 个低通，段是当场相减出来的 —— 存 4 组带通系数既要多存一遍，
又和硬件里真正做的事对不上。边界是 500 / 1000 / 2000 Hz（倍频程），
全项目只在 `src/python/band_design.py` 里定义一次。

别手改这些 txt。要重新生成，两步（都在仓库根目录）：

    python src/python/export_coefficients.py --taps 193       # 先出 txt
    python src/python/export_coeffs_header.py --taps 193      # 再出核 include 的 .h

第一步读 `src/python/band_design.py`（边界和设计法的唯一定义处），
第二步把 txt 转成 `src/hls/fir_coeffs_n193.h`。改边界就改
`band_design.py` 里的 `BAND_EDGES`，然后这两步重跑、再重新综合。

## 怎么跑

在仓库根目录：

    MSYS_NO_PATHCONV=1 HLS_CFLAGS="-DFIR_FIXED=1 -DFIR_N_TAPS=193" \
      cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_csim.tcl"

不带 `HLS_CFLAGS` 就是浮点版。**抽头数的默认值是 193**（和固件一致，
`src/hls/fir_coeffs.h` 里的 `FIR_N_TAPS` 默认值也是 193）——
要跑 65 抽头的对照版必须显式加 `-DFIR_N_TAPS=65`。
**这些开关必须同时作用到核和测试台上**（`run_csim.tcl` 里已经这么做了）——
只给核加的话两边抽头数不一致，测试台会把位移数错，印出看着像结构坏了的数字。
踩过的坑写在 `run_csim.tcl` 里。

输入 `data/audio/test_input.txt`，输出两个：

| 输出 | 是什么 |
| :--- | :--- |
| `data/results/hw_output.txt` | 正常参数（含压缩）的输出，用来和 Python 比 SNR |
| `data/results/hw_transparent_i16.txt` | 压缩比设成 1.0 的输出，**直通检验**用 |

比对：

    python src/python/compare_golden_vs_hw.py

判据是 SNR ≥ 70 dB。实测定点版（16 位采样 × 18 位系数）是 75.4 dB（193 抽头）/
76.0 dB（65 抽头）。

## 直通检验是什么

压缩比设成 1.0 就等价于"不做压缩"，此时按相减式的定义，四段之和恒等于
**输入延迟 D 拍**。所以这一路输出必须逐位等于输入延后 D 拍 —— 位移对不上、
或者有几个点错配，都直接说明结构接错了。实测：193 抽头 D=96、1000 个采样错配 0 个。

这条比 SNR 硬得多：SNR 差一点可能只是量化误差大了，而这条错一个就是结构错。
所以调试时**先看这条**。

## 注意事项

- `test_input.txt` 是去直流后的浮点采样，范围约 -1.0 ~ 1.0。核的接口是
  **int16 / Q1.15**（量程 [-1,1)），测试台负责在入口量化、出口还原 ——
  所以 `hw_output.txt` 是浮点，能直接和 Python 黄金参考逐点比。
- 改动核的内部结构时，测试台和这里的系数生成脚本要跟着改，
  否则两边对不上却不报错。
