# 提交清单 / 合规对照

> 对照《全国大学生嵌入式芯片与系统设计竞赛'2026 FPGA 创新设计赛道选题指南（AMD）》
> §3.3 自主选题赛道逐条核。
>
> 状态标记：✅ 完成 ｜ ⚠️ 有缺口、已写明边界 ｜ ⬜ 待办（不阻塞提交）
> 最后核对：2026-11-01

---

## 一、目录结构（指南 §3.3.5.4）

| 要求 | 本仓库 | 状态 |
|---|---|---|
| `README.md` | 仓库根 `README.md`（构建/运行/验收一条命令） | ✅ |
| `src/` 源代码 | `src/python/`（参考实现与工具）、`src/hls/`（HLS 核）、`src/rtl/`（手写 RTL，4451 行） | ✅ |
| `sim/` 仿真验证 | `sim/hls_csim/`、`sim/rtl_sim/`、`sim/model/`、`sim/run_all_checks.sh` | ✅ |
| `build/` 构建脚本与综合实现报告 | `build/hls/`（32 份 directives + 68 版报告）、`build/README.md` | ✅ |
| `board/` 上板工程与脚本 | `board/overlay/`（位流 + hwh + 构建 tcl）、`board/scripts/`、`board/probes/`、`board/notebooks/` | ✅ |
| `data/` 测试数据与结果 | `data/audio/`、`data/figures/`、`data/results/`（含索引 `README.md`） | ✅ |
| `skill/` 技能包 | 27 条，三档，索引见 `skill/README.md` | ✅ |
| `tools/` 提交打包（本仓库额外） | `make_submission_package.py`，见 §七；指南的推荐结构里没有这一层，属额外的交付工具 | ✅ |
| `report/` 设计报告与大模型协作记录 | 见下表 | ✅ |
| 目录与文件名：小写字母、数字、下划线或连字符 | 目录全小写、无中文、无空格；文件名为小写，仅 `README.md` / `BUILD_GUIDE.md` / `LICENSE` 三个常规大写名 | ✅ |

---

## 二、工具链与板卡声明（指南 §3.3.3.1 / §3.3.3.2）

| 要求 | 落实情况 | 状态 |
|---|---|---|
| 声明具体板卡型号，需在指定板卡上实际跑通 | PYNQ-Z2，`xc7z020clg400-1`；全部数据来自板上实测，非仿真评估 | ✅ |
| Vivado/Vitis 2026.1（推荐）或 2025.2 | **实际用 2020.2**。理由（镜像同代、`audio_codec_ctrl` IP 格式、整批实测绑定）与"脚本可复现 / 数值结果绑环境"的边界，写在 `report/design_report.md` §9 | ⚠️ 已说明 |
| 使用其他版本需在报告中说明并保证脚本可复现 | `rebuild.sh` + `BUILD_GUIDE.md`：三步全 Tcl 化，工具位置用 `VIVADO_BIN` / `VITIS_HLS_BIN` 环境变量给 | ✅ |
| 允许使用公开大模型，须提交体现协作过程的交互记录 | `report/llm_collab_log/` 49 篇，每篇含【任务】【提示词】【模型回答】【哪里错了】【怎么修正】【验证边界】 | ✅ |
| 须将协作过程中沉淀的可复用经验提炼为技能包 | `skill/` 27 条，A/B/C 三档 | ✅ |
| 强烈要求在 GitHub/Gitee 开源，推荐 MIT / Apache-2.0 | 仓库根 `LICENSE` = MIT | ✅ |
| 仓库对外可访问 | 已转 public。2026-11-01 23:59 匿名复查：`private: false`、`visibility: public`、默认分支 `main`、许可 `MIT`、`HEAD` = `1a91bb1`；Release `demo-20261101` 的 `demo_full_20261101.mp4`（59.5 MB）匿名可见 | ✅ |

---

## 三、工程包内容（指南 §3.3.5.1）

| 要求 | 在哪 | 状态 |
|---|---|---|
| 完整工程源码 | `src/` 三套实现；`board/overlay/build_*.tcl` 十份构建脚本 | ✅ |
| 仿真或验证结果，说明功能正确性如何确认 | `sim/run_all_checks.sh` 一条命令跑五项；四关判据见 `report/design_report.md` §5 | ✅ |
| 综合与实现报告，含资源占用、时钟频率、关键性能指标 | HLS 线：`build/hls/reports/`、`data/results/impl_metrics.md`；PL 直连线：`board/overlay/fir_pl_{timing,util}.rpt` | ✅ |
| 上板工程、运行脚本 | `board/overlay/*.bit` + `*.hwh` + `board/scripts/` + `board/probes/` | ✅ |
| 实测输出与参考结果的比对数据 | `data/results/` 全部原始输出；索引 `data/results/README.md` 标明哪份是当前结论、哪些不能引用 | ✅ |
| 可复现的构建脚本 | `rebuild.sh`（csim / ip / bit 三段） | ✅ |

---

## 四、性能与资源（指南 §3.3.4）

| 要求 | 落实 | 状态 |
|---|---|---|
| 性能指标可测量，并与基线对比 | 同一颗芯片 ARM vs PL：16.7×；`data/results/accel_cpu_vs_fpga.md` 附四条必带说明（精度不同 / CPU 是 scipy C / 含 Python 开销 / 乘法对等 579 次） | ✅ |
| 不以显著资源代价换有限性能收益 | 限流对照实测：DSP 202→86 但 LUT 涨 2.6 倍、WNS 从 +1.624 崩到 −0.620，据此判定"不复用乘法器"；`report/design_report.md` §4.3 | ✅ |
| 资源使用率结合性能、时序与扩展余量判断 | 正面承认 DSP 96.8%、WNS +0.090 很薄，并说明"降抽头数"这一格目前无解 | ⚠️ 已如实记录 |
| 从零复现 | 三步构建 + 无板五项验收 + 上板一条命令 | ✅ |
| 工程判断整理为技能包 | 见 §二 | ✅ |

---

## 五、设计报告（指南 §3.3.5.3）

报告本体：**`report/design_report.md`**

| 指南要求的章节 | 报告里的位置 |
|---|---|
| 选题背景与应用价值：解决什么实际问题、**前人做过什么**、创新点 | §1（1.2 列了四份可比工作逐条对照差异） |
| 设计原理与功能框图：算法方案、系统架构、关键模块 | §2、§3 |
| 软硬件划分依据与接口设计 | §2.3、§2.4（寄存器级契约另见 `hardware_interface_spec.md`） |
| 优化过程，含**优化前后的性能与资源对比表** | §4（抽头数 → 拍数轨迹 → DSP/资源 → 数据通路，四张表） |
| 大模型协作记录：提示词策略、模型输出与人工修正 | §6 |
| 技能包提炼过程 | §7（含"从哪次失败抽出来的"对照表） |
| 复现说明 | §8 |

附加章节：§9 工具链与器件声明、§10 未完成项（主动列出五项缺口）。

---

## 六、演示材料

| 项 | 现状 | 状态 |
|---|---|---|
| 演示通路 | `board/overlay/fir_pl.bit`（全 PL 直连）+ 网页验配，启动命令见根 `README.md`「当前演示版」 | ✅ |
| 人耳验收 | 2026-10-15 端到端 40 秒跑通，操作者确认听到处理后的声音；四段增益实时改动 6 次全部接住 | ✅ |
| 演示视频（全片） | [Release `demo-20261101`](https://github.com/JR0425/pynq-multiband-audio-accel/releases/tag/demo-20261101) 的 `demo_full_20261101.mp4`：59.5 MB / 1920×1080 / 2 分 59 秒，实机演示 + 验收过程；口径与报告 §3、§5 一致。**视频放 Release 不放 Git 历史**：59.5 MB 超过 GitHub 单文件建议体积，且历史里的大二进制删不掉 | ✅ |
| 海报（中／英） | **已删除**（原 `report/poster/*.pptx`，见 git 历史 `e1e1d84`）：英文版单页含作者姓名与校名，与指南第六章「不包含队伍信息」冲突；且版面为 16:9，非 A4。决赛前需按 A4 重出一版无队伍信息的英文海报 | 待重做 |

---

## 七、交付包（打包脚本）

指南要求材料一次性打包提交。打包不用"右键压缩整个目录"，用脚本
`tools/make_submission_package.py`：

```bash
python tools/make_submission_package.py          # 产物落 dist/，文件名带 commit 与日期
```

它做四件事：

| 行为 | 为什么 |
|---|---|
| 清单只取 `git ls-tree -r HEAD` | 本机目录 661 MB，其中 `pynq_venv/` 564 MB、`.chart-data-*/` 三个隐藏目录 —— 压缩整个目录会一起带走 |
| 内容按 `git cat-file` 取 blob 字节 | 与 clone 后逐字节一致，不受本机 `core.autocrlf` 影响（见 `.gitattributes`） |
| 打包后重算 `fir_pl.bit` / `fir_pl.hwh` 的 md5，与 README 记录比对 | 位流被换过、或内容取错，立即非 0 退出，不会打出一个"看起来正常"的包 |
| 生成 `MANIFEST.txt` 放进包内 | 提交记录、每个文件的 sha256 与大小、md5 复核结果、视频是否包含 |

边界与前提：

- **演示视频不在仓库里**，脚本只从 `report/video/*.mp4` 取。该目录在 git 里不存在（空目录不跟踪），
  所以正常 clone 下来是空的，脚本会打印警告并继续 —— 要带视频，先从
  [Release `demo-20261101`](https://github.com/JR0425/pynq-multiband-audio-accel/releases/tag/demo-20261101)
  下载 `demo_full_20261101.mp4` 放进 `report/video/`，再打一次。`--no-video` 可显式跳过。
- **工作区有未提交改动时默认拒绝打包**（退出码 3），避免打出一份与提交记录对不上的材料；
  确认无误再加 `--allow-dirty`。
- 产物 `dist/` 已在 `.gitignore` 里，不会被自己打进去。

---

## 八、提交前 30 分钟自检

```bash
# 1. 无板验收（五项，任何一项挂了返回非 0）
bash sim/run_all_checks.sh

# 2. 判断有没有把不该进仓库的东西带进去
git status --short
git ls-files | grep -iE '\.(bit|hwh)$' | head      # 位流/hwh 是故意入库的，要有

# 3. 数字自查：确认报告里引用的每个数都能在 data/results 找到原始输出
grep -rn "16.7\|+0.090\|213\|960000" report/design_report.md

# 4. 断链自查（文档里引用了但仓库里没有的文件）
grep -rhoE '`[A-Za-z0-9_./-]+\.(md|py|tcl|sh|v|txt)`' report/*.md report/llm_collab_log/README.md \
  | tr -d '`' | sort -u | while read f; do [ -e "$f" ] || echo "缺: $f"; done
```

第 3 条尤其要跑：**报告里的数必须能追到原始输出**，这是本项目一直守的一条 ——
`data/results/README.md` 就是为此存在的索引。
