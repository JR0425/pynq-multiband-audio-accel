---
name: refactor-path-breakage
description: >
  Move or split directories in a repo whose scripts locate the repo root with
  dirname(dirname(__file__)). Covers the silent path breakage after a move,
  git mv nesting into an existing directory, batch replacements that miss
  freshly-moved files, replacement rules that shadow each other, and
  scripts that assume an untracked scratch directory already exists.
  Use when a script still imports fine but reports "file not found", after
  restructuring top-level folders, or when a bulk path rewrite half-worked.
license: MIT
compatibility: Any repo; git; Python 3; Git Bash / PowerShell
metadata:
  version: "1.0.0"
  updated: "2026-10-30"
---

# 搬目录之后路径全断：五处静默失败

## 什么时候用

- 把一个仓库的顶层目录合并、拆分、改名，脚本里写过 `dirname(dirname(__file__))` 找仓库根
- 用 `git mv` 搬目录，搬完 `git status` 里一堆「重命名后又删除」（`RD`）
- 写了个批量替换路径的循环，跑完还有文件是旧路径
- 脚本报「找不到文件」，但同一个文件 `ls` 明明在

## 什么时候别用

- 只是改文件名、目录层级没变
- 有包管理/安装机制（`pip install -e .`、`pyproject.toml`）的项目 ——
  路径由打包决定，不该有脚本自己算仓库根

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-10-30 | 初版，来自把 `tools/` 拆进 `board/probes/` 与 `sim/model/` 的那一次 |

## 一、`dirname(dirname(__file__))` 是一种隐式契约

**现象**：脚本照样 import 成功，跑到真正用路径的那一步才报找不到文件；
报错位置离改动很远，看着像环境问题。

**原因**：`D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`
在 `tools/x.py`（深度 1）得到仓库根，在 `sim/model/x.py`（深度 2）得到的是 `sim/`。
它不会抛异常，`sys.path.insert(0, D)` 也照样成功。

**命令**：

```bash
# 搬之前先把所有这种写法找出来
grep -rn "dirname(os.path.dirname" --include=*.py .
grep -rn "ROOT = os.path.dirname(HERE)" --include=*.py .
```

搬完每个文件按新深度补 `dirname()`，然后跑**一个真的会读文件**的子命令验证
（`--check-rom`、`--help` 不够，`--help` 不会碰路径）：

```bash
python sim/model/gen_rtl_coeffs.py --check-rom     # 读 sim/hls_csim/ 并写 src/rtl/
python sim/model/ref_model_wdrc.py --mode wdrc     # 读 data/results/ 里的黄金向量
```

## 二、`git mv` 的目标必须是空目录

**现象**：`git mv tools/board_debug board/probes` 之后，文件进了
`board/probes/board_debug/`，索引里 84 条全是 `RD`（重命名后又删除）。

**原因**：目标已存在时，`git mv` 会把源目录**搬到目标里面**，
等于 `mv 源 目标/源名`；用普通 `mv` 再挪出来，索引又和实际不符。

**命令**：

```bash
mkdir -p board/probes
git mv tools/board_debug/* board/probes/     # 搬内容，不搬目录本身
git add -A board/probes tools                # 让 git 自己算重命名
git status --short | grep -c '^R'            # 应该是 < 文件数 而不是 0
```

## 三、批量替换的清单要用 `grep -rln`，不要用 `git grep -l`

**现象**：替换脚本跑完，文档里的路径全改了，脚本目录里的旧路径一条没动。

**原因**：清单是 `git grep -l` 出的，而刚 `git mv` 的文件在索引里的位置还是旧的
（或者还没进索引），根本不在清单里。

**命令**：

```bash
grep -rln "tools/" . --include=*.py --include=*.sh --include=*.md \
  | grep -v "^./.git/\|^./build/vivado"
```

## 四、关于前缀的替换规则，长的必须在前

**现象**：`tools/board_debug/_tmp` 死活替换不到，日志仍然写到旧位置。

**原因**：规则 `tools/board_debug` → `board/probes` 先执行，
把 `tools/board_debug/_tmp` 变成了 `board/probes/_tmp`，
后面那条针对 `_tmp` 的规则再也匹配不上。

**命令**：`sed` 的 `-e` 是按顺序执行的，先写最长的：

```bash
sed -i \
  -e 's|tools/board_debug/_tmp|build/tmp/probes|g' \
  -e 's|tools/_tmp|build/tmp/probes_rounds|g' \
  -e 's|tools/board_debug|board/probes|g' \
  "$f"
```

## 五、脚本要自己建不入库的目录

**现象**：换了一个 `.gitignore` 里的目录当锁/日志目录之后，
`echo $$ > $LOCK` 直接失败（或者在某些 shell 里静默失败），
换台机器克隆就先挂一次。

**原因**：旧目录是手工建的、恰好还在磁盘上；新目录不在版本库里。

**命令**：脚本开头自己建，别依赖历史残留：

```bash
LOCK=build/tmp/probes/pl_build.lock
mkdir -p "$(dirname "$LOCK")"
```

## 验收

```bash
python -m py_compile <所有搬过的 .py>
for f in <所有搬过的 .sh>; do bash -n "$f"; done
grep -rn "<旧前缀>" . --include=*.py --include=*.sh --include=*.md | grep -v "^./.git/"
```

三条全过再提交。第一条只证明语法，第二条只证明 shell 语法，
**真正会断的是路径，靠第三条和「跑一个真读文件的子命令」才算数。**
