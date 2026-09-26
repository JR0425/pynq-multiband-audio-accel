# PYNQ 多频段音频加速项目技能包 (Skill Package)

本技能包封装了在 PYNQ-Z2 平台上开发实时多频段音频处理加速核的完整工作流、模板与避坑指南。

## 📁 目录说明
- `prompts/`：常用大模型提示词工作流（FIR、FFT、DRC 设计场景）。
- `templates/`：HLS C++ Testbench 模板、硬件接口需求规格模板。
- `checkers/`：自动化校验脚本（音频长度校验、板子连通自检、串口控制台）。
- `pitfalls/`：踩坑记录清单（PYNQ 板级连通、Overlay 加载、音频算法、GitHub 网络等）。

## 🚀 如何使用本技能包？
1. **新手入门**：先阅读 `pitfalls/` 中的踩坑清单。
2. **设计算法**：使用 `prompts/` 中的模板与大模型对话。
3. **软硬件联调**：参考 `templates/` 中的接口规格。
4. **自动化测试**：使用 `checkers/` 校验数据完整性。