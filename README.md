PYNQ Multiband Audio Accelerator

基于 PYNQ-Z2 的实时多频段音频处理加速平台。利用 FPGA 的并行性实现低延迟的听力辅助算法（多频段动态范围压缩），解决纯软件实时性不足与专用芯片流片成本高的问题。

项目结构

· src/ - HLS 核与测试台 (src/hls/)、RTL 与 Python 源代码
· sim/ - 仿真素材（C 仿真用的系数表在 sim/hls_csim/；测试台本身在 src/hls/fir_tb.cpp）
· build/ - 构建脚本与依赖清单 (requirements.txt)
· board/ - 上板工程、Overlay 文件与 Jupyter Notebook
· data/ - 测试音频、频谱图、性能数据表
· skill/ - 大模型协作技能包 (提示词、模板、校验脚本、踩坑清单)
· report/ - 设计报告草稿与协作日志

快速开始

1. 环境配置

推荐使用 Miniconda 创建 Python 3.9 环境。在终端中执行：

2. 运行 Python 软件基线

在终端中执行以下脚本：

运行后，结果图片将保存在 data/figures/，处理后的音频在 data/audio/。

3. 硬件加速与上板

· 板级通路已验证：在 PYNQ-Z2 上加载参考 overlay 并完成端到端跑通，数据见 data/results/reference\_overlay\_metrics.md。
· 自研 HLS 加速核的编译与综合在 src/hls/。
· 上板测试脚本见 board/notebooks/test\_multiband.ipynb。

性能基线

· 软件基线：data/results/baseline\_metrics.md
· 板级通路验证（开源参考 overlay）：data/results/reference\_overlay\_metrics.md
· 自研加速核的资源与频率（Vivado 综合 + 布局布线实测）：data/results/impl\_metrics.md
　选定配置为「相减式 3 低通 / 193 抽头 / 定点 16×18 位 / 乘法器不限流」：
　3962 LUT (7.5%) / 5514 FF (5.2%) / 202 DSP (91.8%) / 51.5 BRAM (36.8%)，
　在 100 MHz（10 ns）约束下 WNS +1.624 ns，时序收敛。
　（独立综合（OOC）的数字，不含外围 AXI / 音频通路；并进 overlay 后须重新量。）
· 自研加速核的端到端延迟：待上板实测

文档与日志

· 设计报告草稿：report/design\_report\_draft.md
· 大模型协作日志：report/llm\_collab\_log/
· 技能包：skill/

开源协议

本项目基于 MIT 协议开源，详见 LICENSE。

