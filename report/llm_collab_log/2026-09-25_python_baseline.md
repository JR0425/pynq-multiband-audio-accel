1 任务：编写 Python 音频生成与频谱分析脚本，跑通本地软件基线。

2 问题（团队需求）：需要脱离硬件，先用 Python 生成测试音频并画频谱图，为后续上板实测提供对比基准。

3 应用模型：DeepSeek

4 模型回答：AI 建议放弃 PowerShell 切换为 cmd 终端，并直接使用绝对路径 `D:\Users\ASUS\miniconda3\python.exe` 运行。针对报错，建议使用阿里云 HTTP 镜像源配合 `--trusted-host` 参数重新安装 numpy 等缺失的依赖库，以绕过 HTTPS 阻断。

5 收获：成功生成 3 秒双声道测试音频 `test_tone.wav`，并画出频谱图 `baseline_spectrum.png`。学会了 Windows 下 Miniconda 环境失效时通过绝对路径强制运行的方法，以及通过 HTTP 镜像源绕过 SSL 断连安装包。