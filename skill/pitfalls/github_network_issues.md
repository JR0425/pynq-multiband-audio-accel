# GitHub 网络连接与认证避坑指南

## 症状
1. `Failed to connect to github.com port 443 after 21138 ms: Could not connect to server`
2. `remote: Invalid username or token. Password authentication is not supported for Git operations.`
3. `Recv failure: Connection was reset` 或 `502` 报错。

## 原因
1. 国内网络对 GitHub 域名的 DNS 污染及 IP 封锁。
2. GitHub 已全面禁止使用账号密码进行 Git 操作，必须使用 Personal Access Token（PAT）或 SSH 密钥。
3. 网络代理软件对 HTTPS 握手有干扰。

## 解决方案
1. **生成 Token**：去 GitHub `Settings -> Developer settings -> Personal access tokens (classic)` 生成，勾选 `repo` 权限。
2. **切换协议**：`git config --global http.version HTTP/1.1`
3. **使用稳定网络**：手机开热点给电脑连接，或使用 Watt Toolkit 开启 GitHub 加速。
4. **重试推送**：`git push -u origin main`，弹出浏览器点授权，或在终端输入 Username（GitHub用户名）和 Password（粘贴 Token，屏幕不显示字符）。
5. **兜底方案**：若 GitHub 始终无法推送，可先用 Gitee 作为临时仓库。