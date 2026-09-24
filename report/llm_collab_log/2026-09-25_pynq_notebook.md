1 任务：创建 PYNQ 板上测试 Jupyter Notebook 模板。

2 问题（团队需求）：硬件 HLS 加速核完成后，需要一个 Python 脚本来加载 Overlay、分配 DMA 缓冲区、调用硬件计算并记录耗时，以便与 Python 软件基线对比。

3 应用模型：DeepSeek

4 模型回答：AI 建议在 board/notebooks/ 目录下提前准备好 Jupyter Notebook 模板，包含 Overlay 加载、音频去直流、DMA 内存分配以及耗时的测量逻辑。由于具体寄存器地址待定，先使用伪代码占位。

5 收获：提前准备好了上板测试的软件骨架，确保技术队友硬件成功后，能在 10 分钟内跑通端到端测试，不必从零开始写板级 Python 脚本。