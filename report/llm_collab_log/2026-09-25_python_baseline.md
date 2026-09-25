【任务】搭建 Python 音频开发环境，生成测试音频并完成频谱分析脚本。

【提示词】
「Miniconda 下载安装报错（HTTP 000），怎么换源？」
「运行脚本报 ModuleNotFoundError: No module named 'numpy'，怎么解决？」

【模型回答】
AI 建议换用中科大/阿里云镜像源安装，并放弃 conda 创建环境，改用本地 Python 的 venv 虚拟环境。针对模块缺失，建议用绝对路径运行 Python 并单独安装依赖包。

【哪里错了】
1. 清华镜像站拒绝访问，中科大源拉取元数据失败（HTTP 000 CONNECTION FAILED）。
2. VS Code 终端（PowerShell）无法正确识别和激活 Conda 环境，命令一直挂起。
3. 使用绝对路径运行脚本时，报 numpy 缺失。

【怎么修正】
1. 放弃 PowerShell，切换为 cmd 终端。
2. 直接使用绝对路径调用 Python 解释器：`D:\Users\ASUS\miniconda3\python.exe`，并为其单独安装包，绕过环境激活失败的问题。
3. 使用 HTTP 阿里云镜像源加 --trusted-host 参数，解决 HTTPS 被阻断的问题。

【沉淀】
→ 形成文档《Windows下 Miniconda 环境在 VS Code 终端挂起的避坑指南》，放进 skill/pitfalls/ 目录中。