【任务】
写第一版多频段处理核（HLS C 版），并让它通过 C 仿真、和 Python 基线对得上。

这一步的意义：在这之前所有东西都在 PC 上、在板子外围；从这里开始才是
**真正要变成电路的那段代码**。C 版跑对了，后面综合和上板才有基准可比。

【提示词】
「写初版c核，并且验证和python基线的一致性」
「测试的音频文件确定是48khz吗？」

【模型回答】
按 Python 基线逐行对应着写：`signal.lfilter` → FIR 抽头延迟线，
`np.where(...)` → 压缩函数 `drc()`，`output += compressed` → 逐频段累加。
系数不重新设计，直接用 `sim/hls_csim/fir_coeffs_{1..4}.txt` 同一批文件。

写完 csim 一次通过（`CSim done with 0 errors`），相关系数 **1.000000000**。

【哪里错了】
跑通的过程里翻出三件事，其中一件会**打出假绿灯**。

· **AI 的问题（最严重的一条：比对脚本比错了对象，而且会报"通过"）**

  `src/python/compare_results.py` 拿 `data/audio/test_input.txt`（**输入**）
  当成"Python 基准"，去和 `hw_output.txt`（**输出**）比。

  而当时 `hw_output.txt` 里装的是 `generate_mock_hw_output.py` 生成的假数据
  （输入 + 一点点噪声）。这两样一比，相关系数当然高，脚本会照样打出：

  > ✅ 结论：硬件输出与 Python 基准高度一致

  **什么都没证明。** 脚本原注释里就写着「目前用测试数据占位」——
  危险的地方在于**它长得像一次通过**。

· **AI 的问题（采样率对不上：两套系数根本不是同一组滤波器）**

  **这条是我问出来的。** 我问「测试的音频文件确定是48khz吗？」——
  AI 之前一路按 48 kHz 说下来，从没去核过那个 wav 到底是什么采样率。
  被这一问才回去查，一查就露了：

  `export_coefficients.py` 按 `fs = 48000` 设计系数，
  而 `multiband_baseline.py` 用的 `fs` 是从 `real_voice.wav` 读出来的 ——
  实测那个文件是 **44100**。

  同一个 `firwin` 调用，`fs` 不同，算出来的 65 个抽头就不是同一组数。
  直接拿去对撞没有意义。

  哪边是对的：板载 codec 是 48 kHz（RTL 里写死），
  所以**给硬件的那组（48k）才是对的**。

· **环境的坑**

  - HLS 的 `open_project` 要的是**工程名**，不是路径。
    传 `D:/.../build/hls/csim_proj` 进去直接报
    `[HLS 200-70] The name '...' contains illegal character ':'` —— 冒号和斜杠都非法。
  - `csim_design` 的工作目录是它自己临时建的目录，**相对路径到不了仓库根**。
  - 顺带发现本机有 g++（`E:\vscodedowmload\mingw64\bin`）。

【怎么修正】
1. **黄金参考改用和硬件完全相同的那组系数去算。**
   新写 `src/python/export_golden.py`：读 `sim/hls_csim/fir_coeffs_{1..4}.txt`
   （也就是导出成 `fir_coeffs.h` 给硬件用的那几份），对同一段输入做卷积。
2. **比对脚本改成比"真参考"和"真硬件输出"。**
   新写 `src/python/compare_golden_vs_hw.py`：相关系数 ≥ 0.999、
   最大单点误差 ≤ 1e-3，并且要扫描 ±16 个采样的位移之后再比。
3. `open_project` 改成先 `cd` 到目标目录、再 `open_project -reset csim_proj`。
4. csim 用 `-argv` 把绝对路径传进去；tcl 里加 `unixify` proc 把反斜杠换成正斜杠。
5. 写 C 的时候先拿本机 g++ **3 秒**编一遍验语法，再上 HLS（9 秒以上），省掉来回等。

【沉淀】
→ `skill/pitfalls/hls_csim_setup.md`（新）—— open_project 传路径报错 / csim 工作目录 /
  `-argv` 传绝对路径 / 本机 g++ 先验一遍的省时做法
→ `skill/pitfalls/audio_data_consistency.md`（新）—— **系数与数据的采样率必须对上**，
  否则"软硬件对撞"是假的；顺带：比对脚本的两端必须是**同一套系数**下的输出与参考
→ `src/python/export_golden.py` / `src/python/compare_golden_vs_hw.py`

**实测数据**（2026-09-28，第一版核，`FIR_PARTIAL=1`）：

| 项 | 值 |
|---|---|
| C 仿真 | `CSim done with 0 errors` |
| 相关系数 | 1.000000000 |
| 最佳位移 | +0 |
| 最大单点误差 | 1.554e-07 |

位移是 0，说明两边的**因果延迟结构一致**——
不像 9/26 那次参考 overlay 要先扫到 +13（那是别人的开源项目，延迟结构不同）。
1.554e-07 正是 float32 的舍入量级，与"核用 float、参考用 float64"的预期一致。
