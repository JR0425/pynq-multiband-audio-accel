---
name: github-network-access
description: >
  Push to GitHub from a network where github.com is unreliable: authenticate with
  a personal access token, try the direct push first, and fall back to the Git Data API
  when github.com is blocked but api.github.com still answers. Use when git push hangs
  or reports Could not connect to server, when authentication is rejected, or when a
  connection is reset mid-transfer.
license: MIT
compatibility: Windows 11 + Git Bash
metadata:
  version: "1.1.0"
  updated: "2026-09-24"
---

# GitHub 网络连接与认证避坑指南

## 什么时候用

- git push 连不上、卡住，或报 Recv failure: Connection was reset
- 报 Password authentication is not supported for Git operations
- 要确认这一次到底能不能直连，还是得走 API

## 什么时候别用

- api.github.com 也连不上 —— 那不是这一页覆盖的情况
- 只是 clone 一个公开仓库 —— clone 和 push 走的是不同的口子

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-24 | 初版 |
| 1.1.0 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |

## 症状

1. `Failed to connect to github.com port 443 after 21138 ms: Could not connect to server`
2. `remote: Invalid username or token. Password authentication is not supported for Git operations.`
3. `Recv failure: Connection was reset` 或 `502`

## 原因

1. 国内网络对 GitHub 的 DNS 污染和 IP 封锁。**而且是间歇的** —— 同一天早上不通、下午可能就通了。
2. GitHub 已全面禁止用账号密码做 Git 操作，必须用 Personal Access Token（PAT）或 SSH 密钥。
3. 代理软件会干扰 HTTPS 握手。

## 先试直连，不要默认它不通

这条来回翻过两次：一旦认定「GitHub 一定不通」，就会跳过直连直接上绕行方案，
多花一步，还容易把两边的 commit 哈希搞乱。

```
git config --global http.version HTTP/1.1     # 握手被干扰时先加这句
git push origin main                          # 超时给短一点
```

2026-09-26 和 2026-10-02 都是直连一次成功。**通不通是按天变的，不是一次性结论。**

## 判据：两个域名分别测，别一起测

封的是 **IP**，不是域名。`github.com` 和 `api.github.com` 解析到同一个 /24，
但一个不通的时候另一个常常是通的 —— 所以「网页打不开」不等于「API 也用不了」。

```
curl -sS -m 10 -o /dev/null -w "github %{http_code}\n" https://github.com
curl -sS -m 10 -o /dev/null -w "api    %{http_code}\n" https://api.github.com
```

## 直连不行时：走 Git Data API

`api.github.com` 通的话，用 Git Data API 建 blob → tree → commit → 改 ref，
效果等价于一次 push。

工具：`tools/push_via_api.py`。先跑 `--dry-run`，它会验权限（`perms.push`）
和远端状态（远端 main 必须和本地 origin/main 一致）。

推完**必须对齐哈希**：API 建出来的 commit 和本地不是同一个对象（committer 日期不同），
两边会分叉，下次 `pull --rebase` 会把同样的改动重放一遍。
用 `tools/reconcile_api_push.sh` 改写本地 commit 对齐。

## 认证

1. 生成 Token：`Settings → Developer settings → Personal access tokens (classic)`，勾 `repo`
2. `git push -u origin main`，Username 填 GitHub 用户名，Password **粘 Token**（屏幕不回显）

## 不要用的

**Watt Toolkit**：点开加速页就卡死，进程以管理员权限跑、杀不掉。
它靠改 hosts 工作，而这里封的是 IP 不是解析 —— 方向就不对。
