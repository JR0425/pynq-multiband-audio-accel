# PYNQ Skill 包

这个目录是开发过程中沉淀下来的可复用材料：提示词、测试台模板、校验脚本和踩坑笔记。
写它是为了让**一支没参与过本项目的队伍**能直接拿走用，所以每一篇都按同一套格式写：
frontmatter 里一句能被检索到的描述、正文开头写明什么时候用/什么时候别用、末尾一张修订表。

## 怎么用

1. **按症状查表。** 下面的索引按"你会看到什么"排，不按模块排。先找症状，再打开对应文件。
2. **先读那一篇的「什么时候用 / 什么时候别用」。** 这两节就是这篇的适用范围和失效条件；
   技能包最常被误用的方式，是把它套在前提不成立的环境里。
3. **照命令做。** 每篇的正文都是「现象 → 原因 → 命令」的顺序，复制命令即可，不需要先理解全部机制。

## 分档：换一个题目还能不能用

判据是**不与该项目绑定、换项目换板卡仍可用**。
按这个判据，本目录的 27 篇分三档：

| 档 | 含义 | 条目数 |
|---|---|---|
| A | 与题目、板卡都无关，任何 FPGA/嵌入式题目可用 | 13 |
| B | 任意 PYNQ-Z2 题目可用（换板卡要改端口和 IP） | 8 |
| C | 音频/信号处理类题目可用（依赖 PYNQ 出厂 base overlay 的音频通路） | 6 |

没有第四档。**没有一篇是绑死在本项目的 DRC/FIR 设计上的**——即使 C 档那几篇，讲的也是
base overlay 的音频通路怎么用，换个音频题目照样成立。

A 档里有 4 篇是**工具类**（脚本本体在 `checkers/` 与 `templates/` 下，不是笔记）：
把"优化前后的资源对比表""黄金参考比对""DMA 搬运""寄存器契约→驱动"这四件每人都要重做的事
封装成了能直接跑的东西。判据和其余各篇一致：只依赖报告的字段名、黄金文件格式、
寄存器表的排版和 AXI-Stream 语义，**不含本项目的任何算法**。

## 索引

### A 档：与题目和板卡都无关

| 条目 | 适用场景 | 使用方法 | 已验证的效果 | 不适用 |
|---|---|---|---|---|
| `windows-python-env`<br>`pitfalls/environment_setup_issues.md` | `conda create` 报 HTTP 000；`pip install` 报 SSLError；VS Code 终端里 Python 无输出 | 三条独立处方，按报错选一条 | 本机从 conda 全挂切到 venv + 镜像源，装完 `requirements-python.txt` 并跑通基线 | 板子上的 Python 环境 |
| `github-network-access`<br>`pitfalls/github_network_issues.md` | `git push` 卡住、`Connection was reset`、认证被拒 | 先分别测 `github.com` 和 `api.github.com`，直连不通再走 Git Data API | 本仓库的推送全部经此路径完成 | `api.github.com` 也不通时 |
| `hls-csim-setup`<br>`pitfalls/hls_csim_setup.md` | `open_project` 报 `contains illegal character ':'`；测试台找不到输入文件；每轮 csim 等太久 | 工程名不写路径；输入用绝对路径；先本机 g++ 验语法 | 每轮等待从分钟级降到秒级 | 要综合或实现数据（那是 csynth/impl） |
| `hls-synthesis-directives`<br>`pitfalls/hls_synthesis_and_directives.md` | 给流水线 FIR 选优化指令；加了指令数字没变 | 指令写 Tcl；先限乘法分配；别对嵌套循环用 unroll | 本项目的核按这些结论选型，OOC 结果为 36 拍 @ 100 MHz | 数据类型问题（见下一行） |
| `hls-fixed-point-types`<br>`pitfalls/hls_fixed_point_types.md` | 定点还是浮点；定点反而更慢更大；选位宽 | 只看布局布线实测，csynth 估算会判错方向 | Q1.15 定点核 36 拍；"饱和 LUT"曾被误判成定点开销，实测纠正 | 还没跑布局布线时 |
| `vivado-windows-batch-build`<br>`pitfalls/vivado_windows_build.md` | 批处理构建随机报 `couldn't read file ...`，每次点到的文件都不一样；加了自动重试反而更差；重试报 OK 但产物时间戳不动 | 先数进程：是不是有两个构建循环、两个 vivado；再看报错里有没有 `: No error`（那是 CWD 被删）；最后核对"成功"判据是不是 grep 了脚本自己的回显 | 3600 次打开 12 个脚本 0 失败证明文件系统无辜；单实例重跑 `create_bd_design` 一次过、validate 也过 | 编译本身报语法错（那是代码问题）；单次偶发失败（重试壳本来就该吸收） |
| `audio-algorithm-prompts`<br>`prompts/audio_algorithm_prompt.md` | 让模型设计滤波器、出频谱图、实现 DRC | 套模板，注意模板里标出的参数陷阱 | 本项目算法初版由这些提示词产出，随后人工修正 | 要参数本身——唯一定义处是 `src/python/band_design.py` |
| `hls-testbench-template`<br>`templates/hls_testbench_template.md` | 新起一个 HLS 测试台，先要骨架 | 复制骨架，按接口填端口 | 本项目 `src/hls/fir_tb.cpp` 由它起步 | 改现有测试台（真实接口在 `fir_tb.cpp`） |
| `refactor-path-breakage`<br>`pitfalls/refactor_path_breakage.md` | 拆/并顶层目录之后脚本报「找不到文件」但文件明明在；`git status` 里一堆 `RD`；批量改路径只改了一半 | 先搜 `dirname(dirname(__file__))`；`git mv` 目标建空再搬内容；清单用 `grep -rln`；替换规则按长度从长到短；`mkdir -p` 自建锁目录 | `tools/` 拆进 `board/probes/` 与 `sim/model/` 后，六个脚本补一层 `dirname` 全部恢复，两个模型脚本重跑对拍仍通过 | 只是改文件名、目录层级没变；有打包机制的项目（路径不该由脚本算） |
| **`csynth-report-table`**<br>`checkers/csynth_report_table.py` | 一堆 HLS 版本要出一张"优化前后"的对比表；某个资源数被质疑要现场复核 | `python csynth_report_table.py <reports根目录> -o out.md` | 本项目 68 个版本一次拉成表，`v25_env_indep` 抠出 36 拍 / DSP 200 / LUT 11491，与既有记录逐项吻合 | 要的是实测值（这时要用布局布线报告，不能用 csynth 估算）；想靠 csynth 的 Fmax 区分方案（实测无区分度） |
| **`golden-compare`**<br>`checkers/golden_compare.py` | 硬件/HLS 输出与参考"看着一样但对不上"；相关系数只有零点几 | `python golden_compare.py got.txt ref.txt --delay 96 --passthrough` | 用本项目的 `hw_output.txt` / `python_golden.txt` 跑通；直通检验与最佳位移两项能分别定位"结构错"和"延迟取错" | 两边精度不同却想逐位比（那只能报 SNR，且必须写明精度不同） |
| **`axi-stream-dma-host`**<br>`templates/axi_stream_dma_host.py` | 自研 AXI-Stream 核第一次接 `axi_dma`；传输"成功"但点数变少；主机侧吞吐远低于核的拍数折算 | 拷 `DmaBlockXfer`，改 `IP_NAME` / `MAX_BYTES` | 本项目 DMA 线 65535 点连发十次拿到 25.7 M 采样/秒；长度截断、cache 顺序、计时窗口三个坑都是那轮踩出来的 | 块很小、次数很少（逐字 MMIO 更简单）；要连续流不断字（得在 PL 里加 FIFO） |
| **`register-map-stub`**<br>`templates/register_map_stub.py` | 自研 AXI-Lite IP 的寄存器表写在设计文档里，驱动要跟着写；上板读回不对要排除"地址抄错" | `python register_map_stub.py spec.md -o regs.py` | 从 `report/hardware_interface_spec.md` 生成 14 个寄存器，偏移与契约逐项一致，生成物可直接编译 | 契约本身就是错的（生成器只会忠实复制错误）；位域语义复杂（读清零/写一触发，它只分 rw/ro/wo/rr） |

### B 档：任意 PYNQ-Z2 题目可用

| 条目 | 适用场景 | 使用方法 | 已验证的效果 | 不适用 |
|---|---|---|---|---|
| `pynq-direct-ethernet`<br>`pitfalls/pynq_direct_ethernet_windows.md` | 网线直连无路由器；网卡拿到 169.254.x.x；9090 打不开；重启后 IP 没了 | 两端静态地址 + 避开 `dhclient` 陷阱 | 192.168.2.99 冷启动后可连，冷启动验收清单已跑过 | 板子接路由器自动取 IP 时 |
| `pynq-serial-console`<br>`pitfalls/pynq_serial_console.md` | 不知道板子 IP；Jupyter 打不开；串口中文显示成 `??????` | PowerShell 直开 COM 口，注意三处编码 | 网络全断时靠 COM6 开始/停止程序、读启动日志 | 需要同时开两个串口任务 |
| `pynq-shutdown-and-power`<br>`pitfalls/pynq_shutdown_and_power.md` | 收板过夜；刚改过配置；不确定现在拔电会不会弄坏 SD 卡 | 按三档判断，等 halt 真的结束 | 拔电与卡损坏的判据来自实测 | 只是重启 Jupyter |
| `pynq-board-hang-capture`<br>`pitfalls/pynq_board_hang_capture.md` | 板子不回 ping/SSH；串口全静；同一条命令时好时坏 | 先判断电还是死机，再用 `panic_print=31` 抓栈 | 抓到了 RCU stall 的栈；断电/死机判据在真实死机上验证过 | 板子还在跑实时管线时打重命令 |
| `pynq-overlay-loading`<br>`pitfalls/pynq_overlay_loading.md` | `Overlay(...)` 报 `No Devices Found`；自建 PS-only overlay；硬件与软件比对相关系数低 | 检查 `.hwh` 与 bit 的配套；用互相关而不是逐点比 | `fir.bit` 加载成功，直通模式按群延迟对齐后错配 0 个 | 用出厂 base overlay（开机已灌好） |
| `pynq-jupyter-files`<br>`pitfalls/pynq_jupyter_api.md` | 没有 SSH 但要传文件；PUT/POST 返回 403；要在板上跑 notebook | 带 `_xsrf` 调用 HTTP API，注意根目录不是 `/home/xilinx` | 本项目的脚本上传与结果回收走这条路 | 网络本身不通时（先走串口） |
| `pynq-matplotlib-font`<br>`pitfalls/pynq_matplotlib_font.md` | 板上的图中文变方框；`Glyph ... missing` 警告 | 只给一种字体，或把中文换掉 | 板级图已能进报告 | 在 PC 上画图 |
| `pynq-widget-latency`<br>`pitfalls/pynq_widget_latency.md` | 电平表/读数比滑杆慢好几秒；一个控件拖慢另一个 | 先量再改，绑定核隔离 | 定位到控件刷新频率而非音频通路 | 没有 widget 的纯脚本 |

### C 档：音频/信号处理类题目可用

| 条目 | 适用场景 | 使用方法 | 已验证的效果 | 不适用 |
|---|---|---|---|---|
| `pynq-audio-playback`<br>`pitfalls/pynq_audio_playback.md` | 板子不出声或录音全是底噪；`set_volume` 越界；wav 被拒；读数贴满量程 | 七个坑逐条排查，先看坑 5（耳机开关锁存）和坑 7（读法） | 麦克风 → 核 → 耳机 40 秒实时通路跑通，逐位自检 960000/960000，用户听感确认 | 要把自定义核接进通路（那是另一个话题） |
| `pynq-headset-mic-detection`<br>`pitfalls/headset_mic_detection.md` | 音箱有声但麦没声；领夹麦在笔记本能用、插板子不行；四段耳机直插能用、加转接头就不行 | 先并一个 32 Ω 负载，再按"每次只换一样"隔离 | 领夹麦 + 老耳机当负载 → 二合一 → 板子，首次录到真人声 | 走 `Line-in` 口（不经过 U41）；只是偶尔要插拔几次才有声 |
| `audio-data-consistency`<br>`pitfalls/audio_data_consistency.md` | 板子输出和 Python 基线各自都对但一比就不像；新接的音源采样率不同 | 先统一采样率，再定比对两端是什么 | 一次 1.6 倍的虚高加速比被这条查出来，改成 1.4 倍 | 只是浮点误差级别的差异 |
| `audio-processing-bugs`<br>`pitfalls/audio_processing_bugs.md` | 频谱 0 Hz 处一根巨峰；输出时长和输入不符；两条线看着只有一条 | 去直流、按原长度切片、正确的 FFT 归一化 | 三个症状各对应一次真实事故 | 问题在硬件通路上 |
| `pynq-codec-i2c-access`<br>`pitfalls/pynq_codec_i2c.md` | codec 寄存器全读回同一个常数（0x00 或 0x01）；写入报成功但回读不变；`0x4023` 读到 0x01 而不是 0xE5/0xF3 | **先确认 PL 配置了没有**（地址脚由 PL 驱动），再按 load→读→init→读→验 bit1 的顺序走；被写拧就断电重上电 | 出货 overlay 下拿到 `0x4014=20 0x4019=33 0x4023=E5`，`codec_init()` 后变 `0x401C=21 0x401E=41 0x4023=F3`；顺带纠正早期写错的 unmute 值 `0x01/0x01/0xE7` | 只是音量偏小（那是增益问题）；I2C 总线本身不通（先 i2cdetect） |
| `pl-internal-tone-diagnosis`<br>`pitfalls/pl_internal_tone_diagnosis.md` | 处理后的音频没声但计数器全绿；分不清"输入是静音"还是"输出链路断"；没有示波器却要判断引脚有没有波形 | 在 PL 里造 440 Hz 方波顶替处理链输出（一个控制位、不动既有通路），听到即证明播放链路全通 | 一条测试音把怀疑范围从"整条链"砍到"音源侧"；同一颗位流里 `0x0B` 响、`0x03` 吹气响，两段同时成立 | 连测试音也不响（那才轮到电气层）；需要精确测频响/THD（这只做定性） |

## 每条是从哪次失败里总结出来的

规则要求技能包写明来源。这里的原则是：**每篇的「症状」和「原因」两节写的就是那次事故本身**，
不是事后编的例子。几个代表性的：

- `pynq-board-hang-capture`：板子连续静默硬死，`hung_task`/`softlockup`/`sysrq`/`ftrace` 在
  这个内核里全被裁掉，最后只剩 `panic_print=31` 一条路。抓到的 RCU stall 栈就是那次的产物。
- `audio-data-consistency`：加速比虚高，是因为两边跑的不是同一套系数、同一个采样率。这类错误
  不会报错，只会让数字好看。
- `pynq-audio-playback` 坑 5：耳机开关只在插头插入的上升沿检测一次并锁存，所以要**拔到电平表
  跳起来**为止——实测拔到第 35.5 秒才跳。
- `pynq-audio-playback` 坑 7：读数贴满量程被当成自激，查了三个晚上，根因是 24 位补码没做符号
  扩展把数字零读成了 rms 46341。
- `hls-fixed-point-types`：定点版反而多出 LUT，追下去是饱和逻辑的开销，不是"定点本身"。
- `pynq-headset-mic-detection`：领夹麦在笔记本上是好的、插板子就没声。五样东西一起换分不清谁的问题，
  最后是"四段耳机直插能用"这一个对照把范围缩到插接件上，再靠"绿色那一路有没有 32 Ω"定案。
- `refactor-path-breakage`：把 `tools/` 拆成 `board/probes/` 和 `sim/model/` 之后，
  「往上两级找仓库根」的六个脚本一声不响指到了 `board/`，`git mv` 还多套了一层目录。
  五条规则各对应那天的一次静默失败。

## 验证过的环境

本目录所有内容都出自同一套环境，每篇 frontmatter 里的 `compatibility` 是它的一部分证据：

- 板级：PYNQ-Z2 镜像 2.7.0，内核 `5.4.0-xilinx-v2020.2`（armv7l）。
- 工具链：Vivado / Vitis HLS 2020.2，目标器件 `xc7z020clg400-1`。
- 主机：Windows 11 + Git Bash / PowerShell。
- 板上 Python 3.8（镜像自带），主机 Python 3.9+。

不要把这个目录里的结果当成"换版本也成立"。换镜像、换板卡、换工具版本之前，先核对该篇
`compatibility` 里写的那几项。

## 格式约定

frontmatter 按官方 Agent Skill（`Xilinx/ross-ai-assistant` 里那套 `SKILL.md`）的写法：
`name`、一个塞满症状词和问法的 `description`（agent 就是拿这个字段做匹配的）、`license`、
`compatibility`、`metadata.version`。

有两件事**故意没照抄**：

- 编号式 `Step 0 … Step N` 工作流骨架。那套形状适合"驱动工具产出文件"的技能；这里的多数条目
  是诊断型的，写的是出了什么问题、怎么认定的、量到了什么，硬套步骤只增加结构不增加信息。
- 一篇一个目录。这些笔记被协作日志和其它脚本按路径引用着，挪位置会让链接失效，而没有任何收益。

## 校验脚本

`checkers/` 下是七件能直接跑的小工具：

| 脚本 | 用途 |
|---|---|
| `check_board_connection.bat` | 一次测完 ping / 22 / 8080 / 9090，按结果给出"断电、死机还是服务没起"的判断 |
| `pynq_serial_console.ps1` | 打开 COM 口读串口 |
| `pynq_serial_send.ps1` | 往串口发一行命令 |
| `check_audio_length.py` | 比对两个 wav 的时长和采样率 |
| `pynq_jupyter_files.py` | 走 Jupyter HTTP API 传文件、跑 notebook |
| `csynth_report_table.py` | **通用**：一堆 HLS 报告 → 一张性能/资源对比表（拍数、II、DSP、LUT、FF、BRAM） |
| `golden_compare.py` | **通用**：黄金参考比对（最佳位移 → 逐位 → SNR → 直通检验），带 0/1 退出码 |

`templates/` 下有两件能直接拷走用的骨架：

| 模板 | 用途 |
|---|---|
| `hls_testbench_template.md` | HLS C++ 测试台最小骨架 |
| `axi_stream_dma_host.py` | **通用**：AXI DMA 整块往返，含 cache 维护与状态轮询的正确形状 |
| `register_map_stub.py` | **通用**：markdown 寄存器表 → Python 访问器 + 读回自检 |
