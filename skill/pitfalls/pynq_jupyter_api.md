---
name: pynq-jupyter-files
description: >
  Move files to and from a PYNQ-Z2 and run notebooks headless over the Jupyter
  HTTP API. Use when the board has no SSH or shared folder but files must be
  transferred, when a PUT or POST returns 403 because of a missing XSRF token, when an
  uploaded file lands in an unexpected directory, when files generated on the board are
  invisible in the Jupyter file list, or when a notebook has to be executed without
  opening a browser.
license: MIT
compatibility: PYNQ-Z2 image 2.7.0, Jupyter 5.x
metadata:
  version: "1.0.1"
  updated: "2026-09-27"
---

# 用 Jupyter 的 HTTP 接口传文件 / 跑 notebook

板子没有 SSH、没有共享目录，但 PYNQ 自带一个 Jupyter 服务（9090 端口），
它的文件接口够用了。实测：PYNQ-Z2 v2.7.0，Jupyter 5.x，2026-09-27。

现成脚本：`skill/checkers/pynq_jupyter_files.py`

```
python skill/checkers/pynq_jupyter_files.py ls
python skill/checkers/pynq_jupyter_files.py put <本地文件> <板上路径>
python skill/checkers/pynq_jupyter_files.py get <板上路径> <本地文件>
```

## 什么时候用

- 板子没有 SSH、没有共享目录，但要传文件或取结果
- 非 GET 请求返回 403，而且不提示缺什么
- 要在板子上不带界面跑一遍 notebook
- 脚本在板子上生成的 wav 在 Jupyter 文件列表里看不到

## 什么时候别用

- 网络不通 —— 先走串口
- 只是想看一个文本文件 —— 串口 cat 更快

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-27 | 初版 |
| 1.0.1 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |

## 坑 1：不带 `_xsrf` 的所有非 GET 请求都被拒

现象：登录接口返回 403，而且**不提示缺什么**。

原因：Jupyter 对非 GET 请求查 CSRF。token 在登录页上，必须**同时**作为 cookie 和
`X-XSRFToken` 请求头回传。

命令：

```python
# 1. 先 GET /login，从页面里抠出 _xsrf
html = opener.open(BASE + "/login").read().decode("utf-8", "replace")
xsrf = re.search(r'name="_xsrf"\s+value="([^"]+)"', html).group(1)

# 2. 用 _xsrf + 密码 POST /login（密码是 xilinx）
opener.open(BASE + "/login", urllib.parse.urlencode(
    {"_xsrf": xsrf, "password": "xilinx"}).encode())

# 3. 之后每个 PUT/POST 都要带上这个头
req.add_header("X-XSRFToken", xsrf)
```

## 坑 2：Jupyter 的根目录不是 `/home/xilinx`

是 `/home/xilinx/jupyter_notebooks`。

所以 `put` 的路径写 `verify_audio_playback.py`，落到的是
`/home/xilinx/jupyter_notebooks/verify_audio_playback.py`，不是 `/home/xilinx/`。
要放别处，先 put 再回串口 `mv`。

### 反过来也成立：**板子上脚本的输出**要落在根目录里

这一条 2026-10-01 又撞了一次，而且方向是反的。

`board/scripts/fir_audio_loop.py` 原来把四个 wav 存到 `/home/xilinx/`。
文件确实生成了（串口 `ls` 看得见），但**在 Jupyter 文件列表里一个都看不到** ——
它只暴露自己的根，看不到上一级。结果是"跑完了、文件在、下不下来"。

改：脚本里的输出目录写成 `/home/xilinx/jupyter_notebooks`。

判据：跑完 `python skill/checkers/pynq_jupyter_files.py ls` 里能看到那几个文件，
而不是在串口里 `ls` 能看到就算数。

已经落在那儿的文件，回串口 `cp` 一次就行：

```
cp -n /home/xilinx/loop_*.wav /home/xilinx/jupyter_notebooks/
```

## 不带界面跑一个 notebook

改完 notebook 不用手点，直接在板子上跑一遍验证：

```
cd /home/xilinx/jupyter_notebooks && echo xilinx | sudo -S env XILINX_XRT=/usr \
    PATH=/usr/local/share/pynq-venv/bin:$PATH \
    /usr/local/share/pynq-venv/bin/jupyter-nbconvert --to notebook --execute \
    --inplace w2_audio_playback.ipynb
```

- `--inplace` 把输出写回同一个文件，之后用 `get` 拉回本地就能看结果。
- `PATH` 要给上，不然 nbconvert 找不到内核。
- **它只报错、不报"警告"** —— 图里的字体问题（见
  `skill/pitfalls/pynq_matplotlib_font.md`）它会静默放过。跑通了不等于图是对的，
  要把图拉下来亲眼看。

## 端口

板子 9090，PC 网卡 192.168.2.1，板子 192.168.2.99。
网络没通时用串口，见 `skill/pitfalls/pynq_serial_console.md`。
