# 截图索引

这个目录存项目过程中的截图。**图里的字是搜不到的**，所以每张图在这里配一行说明
（时间 + 这是什么 + 为什么它重要），引用它的日志里也会再写一遍。

命名规则：纯英文、小写、短横线，编号往后排。

---

## 现有截图

- `log-004-board-jupyter.png` —— 2026-09-25 15:56，PYNQ-Z2 冷启动后浏览器访问
  `http://192.168.2.99:9090/tree?`，Jupyter 正常列出板载文件系统。
  **板子联网跑通的第一份证据。**

- `log-005-board-serial.png` —— 2026-09-26 16:58，micro-USB 线直连板子的串口控制台，
  最后一行 `xilinx@pynq:~$`。**不走网络就能进板子**，和 log-004 是两条独立通道。

- `log-006-ref-overlay-sw-fir.png` —— 2026-09-26 16:48，板载浏览器里跑参考项目
  `FIR_accel.ipynb` 第 4 格（软件版 FIR），软件耗时 **0.0922 s**。

- `log-007-ref-overlay-input-signal.png` —— 同上第 3 格：输入信号是怎么造出来的
  （200 kHz 主信号 + 12 MHz / 46 MHz 噪声，100 MHz 采样，20 万点）。

- `log-008-ref-overlay-hw-accel.png` —— 同上第 6 格：硬件耗时 **0.00469 s**，加速 19.67 倍。
  这是**开源参考 overlay** 的数，不计入本项目指标，详见
  `data/results/reference_overlay_metrics.md`。

- `log-009-ref-overlay-hw-driver.png` —— 同上第 8 格（带驱动的写法）0.1064 s，**比第 6 格慢**。
  原因是这版每次调用都重新申请内存，把申请时间也算进去了。
  留这张是为了说明"同一件事、量法不同，数字差很多"。

- `log-010-board-audio-input-waveform.png` —— 2026-09-27 17:19，`w2_audio_playback.ipynb`
  第 2 格。板子自己生成的 440 Hz 正弦波：48000 Hz、144000 帧、峰值 4194303。
  先有一段"已知干净"的输入，后面听到杂音才能判断是素材的问题还是板子的问题。

- `log-011-board-audio-playback.png` —— 同上第 3、4 格。播放 3 秒音频**墙钟 2.97 s / CPU 2.97 s**；
  录音**2.96 s / 2.96 s**，CPU 时间贴着墙钟走 = 一个核被占满。
  **这是本项目"要把处理搬到 FPGA"的立论。**

- `log-012-vivado-gui-get-parts.png` —— 2026-09-27 18:47，Vivado 2020.2 的 Tcl Console 里
  敲 `get_parts xc7z020clg400-1`，原样返回。**W1 最后一项验收**：器件库完整、图形界面能用。

- `log-013-own-overlay-serial-console.png` —— 2026-10-01 16:31，PuTTY 串口 COM6 连上板子，
  停在 `xilinx@pynq:~/jupyter_notebooks$`。说明后面那步是在**板子本地**跑的，不是从电脑转发。

- `log-014-own-overlay-audio-notebook.png` —— 2026-10-01 16:37，板载 Jupyter 里跑
  `board/notebooks/w3_own_overlay_audio.ipynb`：加载**自建的** `audio.bit` 成功，
  `audio_codec_ctrl_0` 落在 `0x43C00000`（和官方 base overlay 的地址对得上），
  `audio` 对象类型是 `AudioADAU1761`，放 3 秒 440 Hz 正弦波，播放 **2.97 s / CPU 2.96 s**。
  **这是"我们自己建的工程也能出声"的证据** —— 之前那次出声用的是 PYNQ 出厂预编译的
  base overlay，证明的只是"板子会出声"；这一张证明的是"我们的工程会出声"。
  注意：`audio.bit` 里**没有**加速核，核是下一步（`fir.bit`）才进去的。

---

## 后面要截的

- HLS 综合报告 —— 资源占用、II 表（"性能与资源优化 20 分"的原始证据）
- Vivado block design / Address Editor（架构决策的直接证据）
- 上板跑通的画面 —— 核过声音的串口输出、耳机出声
- 麦克风过核那一步（`fir_audio_loop.py`）的实际输出
- AI 界面里"AI 给错了"那一段（截图 + 文字，两个都要）
