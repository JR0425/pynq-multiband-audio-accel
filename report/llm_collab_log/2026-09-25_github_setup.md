【任务】在 Windows 环境下，为 PYNQ-Z2 音频项目创建 GitHub 仓库并推送初始工程骨架。

【提示词】
「我们现在要建仓库，请教我」
「git push 报错 Connection was reset，怎么办？」

【模型回答】
AI 建议建空仓库 -> 本地 git init -> 建全英文目录骨架 -> 创建 README/.gitignore/LICENSE -> 推送。后续针对网络报错，提供了切换 Token、改用热点、修改 http.version 等方案。

【哪里错了】
1. 推送时提示 GitHub 端口 443 连接超时（Failed to connect to github.com:443）。
2. 认证失败（Authentication failed），因为 GitHub 已全面禁止用账号密码进行 Git 操作。
3. 推送中途报 Recv failure: Connection was reset 和 502。

【怎么修正】
1. 配置 `git config --global http.version HTTP/1.1`。
2. 切换到浏览器授权模式，成功拿到 `Authentication Succeeded`。
3. 最终在手机热点网络下，重新执行 `git push`，成功推送 30 个文件。

**复核补记（2026-10-01）**：按项目分工说明补充检查仓库结构时，确认不能把模型建议的目录列表直接当成提交规范；需要对照团队方案列出的官方推荐目录结构，并明确加入“目录名和文件名必须纯英文”这一硬性要求。当前仓库目录和文件名按此要求检查为英文。原始记录没有留下这一步的同期细节，因此本段标为复核补记，不伪装成 2026-09-25 的原始提示词或原始回答。

【沉淀】
→ 形成文档《国内网络环境下 PYNQ 项目 GitHub 建仓与推送避坑指南》，放进 skill/pitfalls/ 目录中作为团队的踩坑记录。
