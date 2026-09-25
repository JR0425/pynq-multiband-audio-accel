【任务】创建 PYNQ 板上测试 Jupyter Notebook 模板。

【提示词】
「帮我写一个 PYNQ 板上的测试脚本模板，包括加载 Overlay 和 DMA 分配」
「需要提前准备什么来测试硬件加速核？」

【模型回答】
AI 建议在 board/notebooks/ 目录下提前准备好 Jupyter Notebook 模板，包含 Overlay 加载、音频去直流、DMA 内存分配以及耗时的测量逻辑。

【哪里错了】
（无报错，为提前准备工作）

【怎么修正】
按标准流程搭建，待硬件就绪后填入具体寄存器地址即可。

【沉淀】
→ 形成 PYNQ 板上测试脚本标准流程，放进 skill/templates/ 目录中。