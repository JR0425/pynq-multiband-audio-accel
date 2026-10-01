# A and B Delivery Review and Local Reproduction

【任务】
对照项目分工清单，盘点已完成的工作，推进本地仓库中可以独立完成的 Python 基线、可视化、日志和文档交付，并记录依赖负责人或板卡的剩余任务。

【提示词】
「那你现在帮我完成A和B该做的所有事情，并且告诉我哪些没完成」

【模型回答】
检查本地仓库后，修复 Windows GBK 控制台下 Python 基线脚本在输出 `µ` 时异常退出的问题；重新生成当前软件基线 WAV、频谱图、黄金参考和频谱瀑布图；把已有 HLS 输出与新黄金参考复比，补充对比图；更新 README、性能表、报告草稿、提交清单、技能包说明和视频脚本；制作一页英文海报草稿。自研核集成、目标工具链确认和板上端到端测试当时仍待完成。（后两项已于 2026-10-01 当晚完成，证据见 `data/results/accel_cpu_vs_fpga.md` 与 `board/overlay/fir.bit`。）

【哪里错了】
仓库检查发现：基线脚本在默认 Windows 控制台输出 `µs` 时抛出 `UnicodeEncodeError`，导致新音频和图表不能正常写出；README 快速开始没有完整安装和运行命令；报告正文残留旧的 65 抽头、独立带通和 RTL 计划；视频脚本把尚未完成的自研核实时演示写成已实现；性能文件没有将桌面 Python、HLS 核级结果和参考 overlay 板测明确分开。

【怎么修正】
1. 把易触发 GBK 错误的单位输出改成 ASCII `us`，并在本地虚拟环境重新运行 193 抽头基线。
2. 重新生成 `multiband_output.wav`、`multiband_comparison.png`、`python_golden.txt`，新增 `spectrum_waterfall.png`。
3. 用新黄金文件复比仓库中已有 HLS 输出：1,000 点 SNR 为 75.4 dB，最佳位移为 0；直通输出延迟 96 点且 0 个错配。此处重跑的是 Python 对比脚本，未重新执行 Vitis HLS。
4. 新增 HLS/Python 输出对比图和最小 Python 依赖清单；更新 README、软件基线表、报告草稿、提交清单、技能包版本说明及进展视频脚本。
5. 创建并渲染检查一页英文海报草稿，明确标注资源数来自 OOC 实现、自研核板级集成尚待完成。

【沉淀】
→ `src/python/plot_spectrum_waterfall.py`：从生成的 WAV 绘制时间-频率瀑布图。
→ `src/python/compare_golden_vs_hw.py`：在数值比对之外生成 Python/HLS 输出与误差图。
→ `requirements-python.txt`：软件基线最小依赖清单。
→ `report/submission_checklist.md`：按证据状态区分已完成、待负责人确认和待上板事项。

【待办】
→ 在匹配版本的 Vitis HLS 环境重新构建并运行 C 仿真，确认结果可从当前源码复现。
→ 由技术负责人确认目标 PYNQ 镜像、Vivado/Vitis 版本和当前接口规格。
→ 集成自研 HLS 核并上板，完成实时出声、端到端延迟与吞吐测量。
→ RTL 实现完成后再生成三实现对撞图；录制最终演示视频前替换本进展稿中的参考 overlay 镜头。
