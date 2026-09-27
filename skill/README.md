# PYNQ 多频段音频加速项目技能包 (Skill Package)

本技能包封装了在 PYNQ-Z2 平台上开发实时多频段音频处理加速核的完整工作流、模板与避坑指南。

## 📁 目录说明
- `prompts/`：常用大模型提示词工作流（FIR、FFT、DRC 设计场景）。
- `templates/`：HLS C++ Testbench 模板、硬件接口需求规格模板。
- `checkers/`：自动化校验脚本（音频长度校验、板子连通自检、串口读/写、Jupyter 传文件）。
- `pitfalls/`：踩坑记录清单（板级连通、Overlay 加载、音频通路、串口与终端编码、上位机画图、音频算法、GitHub 网络等）。

## 🚀 如何使用本技能包？
1. **新手入门**：先阅读 `pitfalls/` 中的踩坑清单。
2. **设计算法**：使用 `prompts/` 中的模板与大模型对话。
3. **软硬件联调**：参考 `templates/` 中的接口规格。
4. **自动化测试**：使用 `checkers/` 校验数据完整性。

## 适用范围

- 硬件：PYNQ-Z2（Zynq-7020）。其他 Zynq 板卡的引脚、时钟、codec 型号不同，不能直接套用。
- 镜像：PYNQ-Z2 v2.7.0（Ubuntu 20.04，Python 3.8）。
- 工具：Vivado / Vitis HLS 2020.2（与上述镜像配套）。
- 主机：Windows 10 / 11，PowerShell 5.1。

## 失效条件

出现下面任一情况时，本技能包里的命令和结论需要重新实测，不能照抄：

- **换 PYNQ 镜像版本**。`.hwh` 解析、`Audio` 类接口、板载 overlay 的 IP 清单都可能变
  （例如 `Audio.load()` 接受的 wav 格式、`set_volume()` 的取值上限）。
- **换板卡型号**。`HP + Mic` 与 `Line-in` 两个 3.5mm 口的位置与用途、串口芯片、
  IP 地址默认值都不同。
- **换成自建 overlay**。"搬运是 CPU 干的"这类结论只对**出厂 base overlay** 成立；
  自建 overlay 一旦加上 DMA，结论就反过来。
- **镜像被重烧或换卡**。静态 IP、账号、Jupyter 端口都会回到出厂默认值。
