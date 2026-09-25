# Windows 下 Python 环境搭建与踩坑指南

## 问题一：Conda 镜像源连接失败（HTTP 000）
**症状**：执行 `conda create -n pynq_audio python=3.9` 时报错 `CondaHTTPError: HTTP 000 CONNECTION FAILED`。
**原因**：国内网络对部分镜像源的 HTTPS 握手存在干扰。
**解决**：放弃 `conda create`，改用 Python 自带的 venv 创建本地环境（`python -m venv`）；若必须使用 conda，尝试换成阿里云镜像源。

## 问题二：pip 下载被 SSL 阻断（SSLError）
**症状**：执行 `pip install` 时反复报 `SSLError: EOF occurred in violation of protocol`。
**原因**：网络中间设备对 HTTPS 流量进行了阻断。
**解决**：改用 HTTP 源，并加上信任主机参数：
`pip install numpy scipy matplotlib soundfile jupyter -i http://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com`

## 问题三：VS Code 终端中 Python 命令挂起
**症状**：在 PowerShell 中执行 Python 脚本无反应，光标一直闪烁但不报错。
**原因**：VS Code 的 PowerShell 终端无法正确识别和激活 Conda 环境。
**解决**：将 VS Code 终端类型切换为 `Command Prompt`（cmd），或者直接使用绝对路径调用 Python 解释器。