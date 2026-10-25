# board/ 目录：工件与现状

## 哪些脚本是演示用的

`scripts/` 下分两组，跑演示只用得到第一组。

- **演示入口**：`run_live.sh`（实时通路）、`run_webui.sh` + `fir_webui.py` + `page.html`（浏览器界面）、`fir_live.py`、`fir_core.py`、`fir_selftest.py`、`fir_tune.py`、`fir_audio_loop.py`、`gain_ab_live.sh`、`wdrc_ab_live.sh`、`hearing_tone.py`、`mic_level.py`、`mic_live.py`。
- **开发探针**：`cap_probe*.py`（14 个，2026-10-10 ~ 10-15，用于定位麦克风通路和符号扩展 bug）、`audio_duplex_probe.py`、`audio_overhead_probe.py`、`fir_chunk_sweep.py`、`mic_watch.py`、`acoustic_lat.py`、`robustness.py`、`wdrc_curve.py`、`compare_cpu_fpga.py`、`verify_*.py`、`replay_own_overlay_tone.py`、`audio_stream_test.py`、`rtl_*.py`（5 个，2026-10-25，只测自研 RTL 的三颗实验位流）。

它们留在 `scripts/` 而不单开目录，是因为 `data/results/` 下的原始结果文件按这个路径引用它们。`cap_probe6.py` 和 `cap_probe7.py` 已在头部声明作废：测量方法有缺陷，结论被 `cap_probe11.py` 取代。

## 仓库里的文件

- `overlay/fir.bit` 和 `overlay/fir.hwh`：项目 overlay —— 自研多频段 HLS 核与 PYNQ 音频编解码器集成。由 `overlay/build_fir.tcl` 构建。
- `overlay/fir.hwdef`、`overlay/fir_timing.rpt`、`overlay/fir_util.rpt`：block design 源文件，以及该 overlay 实现后的时序与资源报告。
- `overlay/ps_only.bit` 及其 `.hwh` 元数据：早期的纯播放 overlay 工件，不含自研核。
- `overlay/audio.bit` 及其 `.hwh` 元数据：纯音频里程碑 overlay —— 项目自建的位流，驱动音频编解码器，不含加速器核。由 `overlay/build_audio.tcl` 构建；被 `notebooks/w3_own_overlay_audio.ipynb` 和 `scripts/verify_own_overlay_audio.py` 加载。

### 自研 RTL 的三颗实验位流（2026-10-25）

这三颗**不是演示用的，也从不替代 `fir.bit`**。它们证明手写 Verilog 前端（3 低通 + 相减 + 4 段 + DRC）能在板上逐位跑对，出货通路仍然只有 `fir.bit`。

| 位流 | 结构 | 用途 |
|---|---|---|
| `overlay/fir_rtl.bit` | AXI-Lite 控制 + 双口 BRAM，PS 逐字搬块 | 拍数、块往返、和 `fir.bit` 并排对拍 |
| `overlay/fir_rtl_dma.bit` | `axi_dma` + AXI-Stream，PS 只写地址和长度 | 连续流吞吐（65535 点 × 10 次） |
| `overlay/fir_rtl_dma_cap.bit` | 同上，外加两个直通监视器和抓取器 `fir_wave_cap` | 只出握手时序图，**不出数字** |

- 构建脚本：`overlay/build_rtl.tcl`、`build_rtl_dma.tcl`、`build_rtl_dma_cap.tcl`；系数表由 `coeff_pre.tcl` 生成。每个脚本综合后检查 `$s_dsp`，少于 100 直接报错 —— 曾经出过一颗 DSP=0 的空壳位流。
- 每个位流配套 `*.hwdef`（BD 源）、`*_timing.rpt`、`*_util.rpt`；综合阶段的 `rtl*_synth_util.rpt`。
- 寄存器地址：`fir_rtl.bit` 的 `0x43C00000/0x43C08000` 是输入/输出 BRAM，`0x43C10000` 是 ctrl、`0x43C20000` 是状态；抓取版另有 `0x40400000`（DMA）、`0x43C30000`（抓取器）。具体位定义看探针头部，不要照抄。
- 探针：`scripts/rtl_probe.py`（块模式）、`rtl_dma_probe.py`（DMA 版，`--stream N` 做连发）、`rtl_dual_probe.py`（两个 overlay 同进程并排出图）、`rtl_wave_probe.py`（抓取版出 `report/img/wave_timing.png`）、`rtl_edge.py`（n=0 / n>8192 的越界守卫）。
- **跑法**：把位流和同名 `.hwh` 拷到板上**同一个目录**再在该目录里跑脚本（`Overlay()` 只认同名文件，地址靠 hwh 解析）。这套东西不进 `/home/xilinx/jupyter_notebooks/` 的演示副本。
- 数字一律引 `fir_rtl_dma.bit`（WNS +0.267 ns）；抓取版多了监视器，只用来画图。两版的拍数差 1 拍是计时终点不同，不是核变了。全部实测记录见 `data/results/rtl_vs_hls.md` §9、日志 `report/llm_collab_log/2026-10-25_rtl_on_board.md`。

- `scripts/fir_core.py`：核的寄存器级驱动（AXI-Lite 控制、AXI4-Master 数据搬运）。
- `scripts/fir_audio_loop.py`：麦克风 → 核 → 耳机回环，每次一块。这还是基于块的路径，不是实时那条。
- `scripts/fir_live.py`：麦克风 → 核 → 耳机**实时**通路（边采边播）。经连续 8/10/24 秒运行验证，全部 1.00× 无丢块；24 秒那趟搬了 2,401 块，通过 960,000/960,000 采样逐位一致自检。核接口未动，`fir.bit` 未重建。**2026-10-15 更新：实时监听已通。** 2026-10-13 的"采集输入被驱动到满量程自激"是误判 —— 那是**读数假象，不是硬件问题**。I2S 寄存器的低 24 位是二进制补码，读取时没做符号扩展；直流偏置（≈ 31,679 ≈ 2²³ × 0.966）正好压在符号边界上，数字零被读成 **rms 46,341 = 65535/√2**。真正的拦路虎是 `HP + Mic` 插座上的 **U41**（TI TS3A226AE 自动耳机开关，在插入沿检测一次 CTIA/OMTP 后锁存）：**反复插拔到电平表跳起来为止 —— 实测要 18 秒 —— 然后启动，别再动插头。** 一趟 40 秒运行（4,000 块，40.00 秒音频 / 40.19 秒实际用时 = 1.00×，逐位自检 960,000/960,000）进核 rms 中位 107 / 峰 24,076，用户当场听到自己处理过的声音。那趟四段增益全是 0.0 dB（`LIVE_GAIN_DB` 默认值），证明的是*通路*而不是四频段效果 —— 四频段效果是同一天单独测的（见下面的证据边界）。它还超时 0.19 秒 ≈ **丢 18 块**（能听出的卡顿 —— 采样搬运线程跑满了 10 ms 预算的 99%）。2026-10-13 的探针日志作为错误样例保留，不作为证据。**2026-10-19（WDRC 版）：** 同一条通路以 `LIVE_ATTACK_MS=5 LIVE_RELEASE_MS=100` 跑了 90 秒 —— 8,926 块，89.26 秒音频 / 90.05 秒实际用时（0.99×），丢 78 块，包络与起停时间均生效时逐位自检仍通过 960,000/960,000。
- `scripts/audio_stream.cpp` 和 `scripts/build_audio_stream.sh`：自研的流式音频通路，板上用 gcc 编译。
- `scripts/ksoftirqd_rt.sh` 和 `scripts/install_ksoftirqd_rt.sh`：板挂修复。采样搬运线程是 `SCHED_FIFO 20` 且从不阻塞，会把 CPU0 的 `ksoftirqd` **和 `kworker`**（都是普通 CFS）饿死，约 60 秒后触发 RCU-stall panic。安装脚本注册开机项 `ksoftirqd-rt.service`，把 `ksoftirqd/0`、`/1` 和 `kworker/[01]:*` 提到 `SCHED_FIFO 50`，外加 `ksoftirqd-rt-refresh.timer` 每 30 秒重扫一次（workqueue 会在运行期新建 worker）。以 300 秒运行（丢 61 块，0.20 块/秒，0.998×）和 600 秒运行验证：不挂，59,478 块，丢 539（0.90 块/秒），0.991×，拉取器 212/212 次应答。证据：`data/results/board_hang_evidence_20261017.md`。
- `scripts/audio_stream_test.py`、`scripts/audio_overhead_probe.py`、`scripts/audio_duplex_probe.py`：该流式通路的测试与探针。
- `scripts/run_live.sh`：一条命令启动实时通路。
- `scripts/fir_chunk_sweep.py`：核的块大小扫描。
- `scripts/fir_selftest.py`：直通检验与块连续性检查。
- `scripts/wdrc_ab_live.sh`：WDRC 起停时间的现场 A/B —— 一趟 90 秒运行里 0/0 → 5/100 ms → 0/0 切换，阈值与压缩比全程不动。
- `scripts/compare_cpu_fpga.py`：同板 ARM 基线对核。
- `scripts/mic_level.py`：打印 int16 域的实时输入电平，低于静默阈值的会标出来。`SILENT_RMS = 200`（满量程 32767）—— 阈值是 200，不是 8000；8000 那个值是单位错误，2026-10-02 已修。
- `scripts/mic_live.py`：连续输入电平监视，阈值同为 200，同时打印 rms 和峰，有信号但很轻不会被误判成静默。
- `scripts/fir_tune.py`：读写调参 notebook 与 `fir_live.py` 交换用的两个 `/dev/shm` 文件。
- `scripts/fir_webui.py` 和 `scripts/run_webui.sh`：浏览器调参页，由**板子**在 8080 端口提供服务（标准库 `http.server`，无第三方依赖）。它与 notebook 界面交换同一批 `/dev/shm` 文件，也能开始/停止 `fir_live.py`（与 notebook 相同的板挂修复预检）。从笔记本打开 `http://192.168.2.99:8080/`。外观与布局参照 MIT 许可的 `AyQWERTY/hearing-test` 单页应用；Chart.js 与听力测试流程已去掉，响应曲线、波形和频谱都画在自己的 canvas 上。仅限局域网 —— 没有认证。已用无头 Edge 本地截图验证，2026-10-20 部署到板（`http://192.168.2.99:8080/` 返回 200，`/api/tune` 写入 `/dev/shm/fir_tune.json`）；开始/停止按钮尚未在板上实际用过。**如果 `fir_webui.py` 旁边放着 `page.html`，它就被当作主页提供，内嵌的那份被忽略** —— 这就是换界面的预期方式：文件放进去、浏览器刷新，不用重启。删掉 `page.html` 则回退到内嵌页面。两条路径都已于 2026-10-20 在板上验证。
- `notebooks/w5_tuning_ui.ipynb`：拟合界面。四段增益滑杆加阈值、压缩比、起停时间（2026-10-19 增加）、电平表和实时示波。它只写 `/dev/shm/fir_tune.json`、读 `/dev/shm/fir_status.json`，关闭或重启都不打断音频流。
- `notebooks/w3_own_overlay_audio.ipynb`：加载 `overlay/audio.bit`（纯音频里程碑构建，无加速器核），确认项目自建位流能驱动音频编解码器。
- `notebooks/w2_audio_playback.ipynb`：基线 overlay 音频播放里程碑 notebook。
- `notebooks/test_multiband.ipynb`：集成 `fir.bit` overlay 的板上冒烟测试；用当前的 `fir_core.py` 寄存器驱动，在合成信号上验证直通延迟与压缩。
- `scripts/verify_audio_playback.py`：基线 overlay 音频播放与采集检查。
- `scripts/test_overlay_load.py`：overlay 加载与 IP 清单检查。
- `scripts/verify_fir_hw_e2e.py`：面向暴露预期 FIR DMA 的 overlay 的 DMA FIR 测试流程。其默认的 `fir_accel2.bit` 与 `filter.fir_dma` 不在本仓库中。

## 保持板上环境同步

板上跑的是 `/home/xilinx/jupyter_notebooks/` 下的副本，仓库里改了文件，不拷过去就不生效。由此有两条规矩：

- **`scripts/audio_stream.cpp` 一改，`libaudio_stream.so` 必须重建。** 运行 `sh /home/xilinx/jupyter_notebooks/build_audio_stream.sh`，**用 `xilinx` 用户，不要 sudo**。2026-10-13 漏了这一步，探针结果和 `fir_live.py` 的修复都悄悄测的是旧库。
- **`scripts/mic_level.py` 和 `scripts/mic_live.py` 一改就必须重拷。** 仓库里用的是 `SILENT_RMS = 200`；2026-10-02 之前它们带的 8000 是单位错误，会把有声的麦克风判成静默。两个板上副本已于 2026-10-15 验证与仓库逐字节一致，**没有待重拷项**。
- **板挂修复是系统服务，不是拷文件。** 用 `sudo sh /home/xilinx/install_ksoftirqd_rt.sh` 装一次（脚本在 `board/scripts/`）。它安装 `ksoftirqd-rt.service` 和 `ksoftirqd-rt-refresh.timer`。用 `systemctl is-active ksoftirqd-rt.service`、`systemctl is-active ksoftirqd-rt-refresh.timer` 和 `chrt -p $(pgrep -f '^ksoftirqd/0$')` → `SCHED_FIFO 50` 验证。**SD 卡重刷后要再装一遍**；不装的话实时通路约一分钟后 panic。
- **`scripts/fir_webui.py` 和 `scripts/run_webui.sh` 是新的（2026-10-20）**，当天已拷到 `/home/xilinx/jupyter_notebooks/`。用 `sh /home/xilinx/jupyter_notebooks/run_webui.sh` 启动；脚本会自行 `sudo` 提权，因为页面的开始按钮要拉起了 `fir_live.py`。停页面服务用 Ctrl-C —— 那**不会**停掉音频。走 SSH 的话要分离启动（`setsid nohup sh run_webui.sh > /dev/shm/webui.log 2>&1 < /dev/null &`），否则通道会一直挂到超时。注意板子地址是 **192.168.2.99**；`192.168.2.1` 是笔记本自己的静态地址，那台机的 8080 端口被另一个本地服务占用，所以打错 URL 会返回 `Access Error: 404 -- Not Found`。

板上 `overlay/fir.bit`、`overlay/audio.bit`、`scripts/fir_live.py`、`scripts/mic_level.py`、`scripts/mic_live.py` 和 `libaudio_stream.so` 截至 2026-10-24 都是最新的（md5 核对过，不是假定）。`audio_stream.cpp` 2026-10-13 的改动只动了注释，现有 `.so` 仍然正确。`overlay/fir.bit` / `fir.hwh` 于 2026-10-19 用 WDRC 核（49 拍，`v21_wdrc`）重建过，2026-10-23 又用 `v25_env_indep`（36 拍）重建；位流 md5 `5aa079f29e8d66c21d1d34f1302c3695`，仓库里的副本和板上的一致；`scripts/fir_core.py`、`scripts/fir_live.py`、`scripts/fir_selftest.py`、`scripts/fir_chunk_sweep.py`、`scripts/compare_cpu_fpga.py`、`scripts/audio_duplex_probe.py` 和 `scripts/fir_audio_loop.py` 已于 2026-10-19 重拷并与仓库哈希一致。新寄存器 `drc_attack`（0x58）和 `drc_release`（0x60）只有更新后的驱动会写，那里留 0 就保持旧的无记忆行为。

## 证据边界

自研核的板上结果是 `data/results/fir_audio_loop_run_20261001.txt` 和 `data/results/compare_cpu_fpga_run_20261008.txt`；文字稿是 `data/results/accel_cpu_vs_fpga.md`。录下的 144,000 采样、18 块采集通过了直通恒等检验、零错配，但输入电平近乎静默（有效值 1,342），所以它不能证明麦克风 A/B 压缩演示可闻。合成信号确实演示了压缩。另一趟麦克风 → 核 → 耳机实时运行（`scripts/fir_live.py`）连续 8/10/24 秒全部 1.00× 无丢块，通过 960,000/960,000 采样逐位一致自检；核接口未动，`fir.bit` 未重建。2026-10-15 那趟扩展到 40 秒（4,000 块，40.00 秒音频 / 40.19 秒实际用时，逐位 960,000/960,000），是操作者第一次在耳机里听到自己处理过的声音；它四段增益全是 0.0 dB，证明的是通路而非四频段效果，并且因音频 IP 无 DMA 超时 0.19 秒 ≈ 丢 18 块。四频段效果是同一天单独测的（`data/results/fir_gain_ab_run_20261015.txt`，`scripts/gain_ab_live.sh` 复现）：一趟 66 秒运行中通过 `/dev/shm/fir_tune.json` 实时改写分段增益六次，单段在 0 dB 与 −41.9 dB 之间交替。六次改动全部生效，运行内八次增益变更下逐位自检仍通过 960,000/960,000，速率保持 1.00×，操作者报告听到数次明显变化。早先用对称 +6/0/0/−6 设置的尝试不可闻 —— 增益确实生效了（输入/输出电平比从 1.00 到 1.23）但总响度几乎没变，所以不作为证据引用。WDRC 起停时间通路于 2026-10-19 做过可闻验证（`data/results/fir_wdrc_ab_run_20261019.txt`，`scripts/wdrc_ab_live.sh` 复现）：一趟 90 秒运行，阈值 −36 dBFS、压缩比 4:1 全程不动，通过同一个 JSON 文件实时切换起停时间 0/0 → 5/100 ms → 0/0。两次改动都被接住，逐位自检通过 960,000/960,000，操作者报告两个切换点前后都有明显可闻差异，运行保持 0.99×、丢 78 块 —— 与更早的运行一样是音频 IP 无 DMA 的限制。同板 ARM 对核比对于 2026-10-23 对出货的 36 拍核（`v25_env_indep`）重测：ARM 6.117 µs/采样（3.41× 实时）对核 0.367 µs/采样（56.77× 实时），比值 **16.7×**（`data/results/compare_cpu_fpga_run_20261023.txt`）。2026-10-22 对 49 拍 `v21_wdrc` 位流测得的 12.5× 属于那一版，19.5× 属于 31 拍的 `v19`。口径相同的比对是 ARM float64/scipy 对 PL Q1.15，不与手写 NEON CPU 实现对比。

`data/results/reference_overlay_metrics.md` 记录的是另一项更早的测试：一个单独的开源 27 抽头 FIR 参考 overlay。那个结果只验证板上 DMA 通路，不测量自研核。引用数字时把两者分开。

自研 RTL 那三颗位流给出的是另一套口径：核内 3 拍/采样、65535 点连发 25.7 M 采样/秒（约 536×），但**它们不在演示通路上**，不能和出货链路的 1.00× 实时、也不能和块模式那张 46× 的表并列。逐位对拍只覆盖 4800 点单块与连跑两块；RTL 的 DRC 通路只对 Python 模型，没有和 v25 逐位比（v25 的起停是运行时寄存器，RTL 是编译期参数）。

跑 overlay 测试之前，把位流、`.hwh`、DMA/IP 名、板镜像版本、预期采样格式作为一组确认。不要把 `ps_only.bit` 拿去喂 `verify_fir_hw_e2e.py`；它不暴露该脚本预期的 FIR DMA。
