---
name: windows-python-env
description: >
  Set up a Python environment for this project on Windows when the usual routes
  fail: HTTP 000 from conda mirrors, SSLError on pip install, and a Python command that
  hangs in the VS Code PowerShell terminal. Use when conda create cannot connect, when
  pip install raises SSLError EOF occurred in violation of protocol, or when a Python
  command in VS Code produces no output and never returns.
license: MIT
compatibility: Windows 11
metadata:
  version: "1.0.1"
  updated: "2026-09-24"
---

# Windows 下 Python 环境搭建与踩坑指南

## 什么时候用

- conda create 报 CondaHTTPError: HTTP 000 CONNECTION FAILED
- pip install 报 SSLError: EOF occurred in violation of protocol
- VS Code 终端里跑 Python 没反应、光标一直闪

## 什么时候别用

- 环境已经能跑了 —— 本机是 miniconda 3.10.8
- 问题出在板子上 —— 那是 PYNQ 镜像里的 Python，不是 Windows 这边

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-24 | 初版 |
| 1.0.1 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |

## 一、`conda create` 报 HTTP 000

```
CondaHTTPError: HTTP 000 CONNECTION FAILED
```

国内网络对部分 conda 镜像源的 HTTPS 握手有干扰。

放弃 `conda create`，用 Python 自带的 venv：

```
python -m venv .venv
```

必须用 conda 的话，换阿里云镜像源再试。

## 二、`pip install` 报 SSLError

```
SSLError: EOF occurred in violation of protocol
```

中间设备对 HTTPS 流量做了阻断。改用 HTTP 源并信任主机：

```
pip install numpy scipy matplotlib soundfile jupyter -i http://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
```

## 三、VS Code 终端里跑 Python 没反应

现象是光标一直闪、不报错、也不返回。VS Code 的 PowerShell 终端认不出 conda 环境。

两条路：把 VS Code 的终端类型切成 `Command Prompt`（cmd），
或者直接用解释器的绝对路径调用。
