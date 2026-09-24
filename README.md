# PYNQ Multiband Audio Accelerator

基于 PYNQ-Z2 的实时多频段音频处理加速平台。利用 FPGA 的并行性实现低延迟的听力辅助算法（多频段动态范围压缩），解决纯软件实时性不足与专用芯片流片成本高的问题。

## 📁 项目结构

```text
├── src/                # 源代码
│   ├── hls/            # HLS C++ 加速核源码 (待添加)
│   ├── rtl/            # 自研 RTL FIR 源码 (待添加)
│   └── python/         # Python 软件基线、音频生成、FFT分析、DRC算法
├── sim/                # 仿真验证
│   └── hls_csim/       # HLS C++ Testbench 与测试数据
├── build/              # 构建脚本与依赖清单
│   └── requirements.txt
├── board/              # 上板工程与脚本
│   ├── overlay/        # PYNQ Overlay 文件 (.bit/.hwh)
│   └── notebooks/      # Jupyter Notebook 测试脚本
├── data/               # 测试数据与结果
│   ├── audio/          # 测试音频 (.wav) 与导出的文本激励
│   ├── figures/        # 频谱图、对比图
│   └── results/        # 软硬件性能数据表
├── skill/              # 大模型协作技能包
│   ├── prompts/        # 提示词工作流
│   ├── templates/      # 案例模板
│   ├── checkers/       # 校验脚本
│   └── pitfalls/       # 踩坑清单
└── report/             # 报告与日志
    ├── design_report_draft.md
    ├── llm_collab_log/ # 大模型协作日志
    └── poster_en/      # 英文海报 (待添加)
🚀 快速开始 (Quick Start)

1. 环境配置

推荐使用 Miniconda 创建 Python 3.9 环境：

```bash
conda create -n pynq_audio python=3.9
conda activate pynq_audio
pip install -r build/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
```

2. 运行 Python 软件基线

```bash
# 生成测试音频
python src/python/generate_test_audio.py

# FFT 频谱分析
python src/python/analyze_spectrum.py

# 单频段 FIR 滤波
python src/python/fir_filter_baseline.py

# 4 频段多频段 DRC 处理
python src/python/multiband_baseline.py

# 导出 C++ 测试数据 (供 HLS 仿真使用)
python src/python/export_test_data.py
```

运行后，结果图片将保存在 data/figures/，处理后的音频在 data/audio/。

3. 硬件加速与上板 (待技术队友完成)

· HLS 加速核编译与综合在 src/hls/。
· 上板测试脚本见 board/notebooks/test_multiband.ipynb。

📊 性能基线

软件基线数据已记录在 data/results/baseline_metrics.md。
（硬件资源占用与端到端延迟数据待上板后填入）

📄 文档与日志

· 设计报告草稿：report/design_report_draft.md
· 大模型协作日志：report/llm_collab_log/
· 技能包：skill/

📜 开源协议

本项目基于 MIT 协议开源，详见 LICENSE。

```

### 🛠️ 第三步：保存
按键盘上的 **`Ctrl + S`**（确认 `README.md` 旁边的小白点消失）。

### 🚀 第四步：终端提交推送
回到终端，依次执行这三行：
```bash
git add .
```

```bash
git commit -m "docs: finalize README with full structure and quick start"
```

```bash
git push
```

---
