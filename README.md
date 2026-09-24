PYNQ Multiband Audio Accelerator

基于 PYNQ-Z2 的实时多频段音频处理加速平台。利用 FPGA 的并行性实现低延迟的听力辅助算法（多频段动态范围压缩），解决纯软件实时性不足与专用芯片流片成本高的问题。

项目结构

· src/ - HLS、RTL 与 Python 源代码
· sim/ - 仿真验证与 HLS C++ Testbench
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

3. 硬件加速与上板 (待技术队友完成)

· HLS 加速核编译与综合在 src/hls/。
· 上板测试脚本见 board/notebooks/test_multiband.ipynb。

性能基线

软件基线数据已记录在 data/results/baseline_metrics.md。
（硬件资源占用与端到端延迟数据待上板后填入）

文档与日志

· 设计报告草稿：report/design_report_draft.md
· 大模型协作日志：report/llm_collab_log/
· 技能包：skill/

开源协议

本项目基于 MIT 协议开源，详见 LICENSE。
