# 提交清单 / 合规对照

> 对照《全国大学生嵌入式芯片与系统设计竞赛'2026 FPGA 创新设计赛道选题指南（AMD）》
> §3.3 自主选题赛道逐条核。
>
> 状态标记：✅ 完成 ｜ ⚠️ 有缺口、已写明边界 ｜ ⬜ 待办（阻塞提交，必须先处理）
> 最后核对：**2026-11-02 晚**（上一版 2026-11-02 下午）
>
> **11-02 晚这一轮改了什么**（对着指南逐条复核后的收口，全部可在仓库内验证）：
> 8. 补参赛组别声明（**初级组**）；报告首页与本节同步。
> 9. 预设由三档改**四档**，新增 `show`（+30 dB，压缩器确认动作，实测 2.36%）——
>    原来 `demo` 档同时写"压缩器未启动"与"压缩效果最强"是自相矛盾，且三档里没有一档
>    "压缩器在动、失真可控"。`--selftest` 的断言随之改为四档。
> 10. 新增通用 PYNQ Skill **`skill/templates/pynq_overlay_skeleton.py`**（带 `--selftest`），
>    技能包 34 → **35 条**，A 档 20 → 21；报告 §7 的分档数、笔记篇数一并改正
>    （原 §7 写 A 档 13、笔记 23 篇，与 `skill/README.md` 冲突）。
> 11. 删掉报告里**重复的 §4.5 整节**；新增 §6.7 智能体工作流设计（指南 §3.3.5.3 明确要求）。
> 12. §10 A 组补两行：**演示通路上的单点台阶**（已观测、未定位，`pop_steps_20261031.md`）
>    与**演示通路 CPU 占用未实测**。前者原本在报告里完全没有出现。
> 13. §5.1 把"四段加乘法器没有空间"改成精确口径：DSP 剩 3 个、乘法槽 579/585 剩 6 槽，
>    **卡的是 +0.090 ns 时序**，并给出"先降频换余量再改结构"的顺序。
>
> **11-02 这次改了什么**（都是对着指南逐条复核后发现的真问题，不是润色）：
> 1. 删掉工具链论证里一条**用错对象**的理由（`audio_codec_ctrl` 只属于上一代 `fir.bit`），
>    换成可查证的官方事实（PYNQ 4.0 已移除 Z2 支持）；补上"为什么不升 2024.1"。
> 2. 发现并写明"**演示默认档的 THD+N 从未实测**"（11-01 改了默认参数之后没重测），
>    新增三档运行期预设作为缓解；11-02 晚已在交付位流上补测默认档 0.09%（压缩器未启动），
>    `--selftest` 改成守着"每档只填自己的实测值"。
> 3. 补 §4.0 取舍全貌图（68 版快照现场解析生成，非手画）。
> 4. 统一延时口径（README 1.97 ms 与报告 2.00 ms 不一致 → 全部改为 2.00 ms = 96 采样 @ 48 kHz）。
> 5. §0 一页速览每个数字加"对应位流"列（16.7× 属于块模式位流，不是演示通路）。
> 6. 技能包补 8 条提示词工作流（原来只有 1 条），总数 27 → 34。
> 7. **海报从仓库（含 Git 历史）整体移除**，改由队伍按竞赛渠道单独提交。历史清了两遍：第一遍
>    之后另一处会话把一个仍留着旧历史的本地克隆合并回 `main`，pptx 又跟着 merge 回到了历史里；
>    第二遍重新 `filter-repo` 后强推，`git rev-list --objects --all` 已无 `*.pptx`。

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
| `skill/` 技能包 | 35 条，三档（含 8 条提示词工作流、6 条通用工具/模板），索引见 `skill/README.md` | ✅ |
| `tools/` 提交打包（本仓库额外） | `make_submission_package.py`，见 §七；指南的推荐结构里没有这一层，属额外的交付工具 | ✅ |
| `report/` 设计报告与大模型协作记录 | 见下表 | ✅ |
| 目录与文件名：小写字母、数字、下划线或连字符 | 目录全小写、无中文、无空格；文件名为小写，仅 `README.md` / `BUILD_GUIDE.md` / `LICENSE` 三个常规大写名 | ✅ |

---

## 二、工具链与板卡声明（指南 §3.3.3.1 / §3.3.3.2）

| 要求 | 落实情况 | 状态 |
|---|---|---|
| **声明参赛组别** | **初级组**（报告首页与本清单一致）；器件 `xc7z020clg400-1` 在初级组允许范围内 | ✅ |
| 声明具体板卡型号，需在指定板卡上实际跑通 | PYNQ-Z2，`xc7z020clg400-1`；全部数据来自板上实测，非仿真评估 | ✅ |
| Vivado/Vitis 2026.1（推荐）或 2025.2 | **实际用 2020.2**，但论证换过了：**PYNQ 镜像与 Vivado 成对发版，PYNQ-Z2 镜像停在 3.1.0（2024.1），4.0.0（2025.2）已移除 Z2 支持** —— "2025.2 + PYNQ-Z2"在官方栈里不存在。2024.1 存在但未采用的三条理由单列。全文 `report/design_report.md` §9.1 / §9.2 | ⚠️ 已说明（可查证） |
| 使用其他版本需在报告中说明并保证脚本可复现 | `rebuild.sh` + `BUILD_GUIDE.md`：三步全 Tcl 化，工具位置用 `VIVADO_BIN` / `VITIS_HLS_BIN` 环境变量给 | ✅ |
| 允许使用公开大模型，须提交体现协作过程的交互记录 | `report/llm_collab_log/` 49 篇，每篇含【任务】【提示词】【模型回答】【哪里错了】【怎么修正】【验证边界】 | ✅ |
| 须将协作过程中沉淀的可复用经验提炼为技能包 | `skill/` **35 条**索引，A/B/C 三档；指南四类都覆盖：提示词工作流 8 条、踩坑笔记 21 篇、通用工具/模板 6 条（`checkers/` 共 7 个脚本、`templates/` 共 4 个文件）。**含指南 §3.3.2 举例的"Overlay 加载与 register_map 使用范式"专项骨架** `templates/pynq_overlay_skeleton.py` | ✅ |
| 强烈要求在 GitHub/Gitee 开源，推荐 MIT / Apache-2.0 | 仓库根 `LICENSE` = MIT | ✅ |
| 仓库对外可访问 | 已转 public。2026-11-01 23:59 匿名复查：`private: false`、`visibility: public`、默认分支 `main`、许可 `MIT`、`HEAD` 为提交前的最后一次 commit（**不抄具体 hash**：2026-11-02 重写历史后全部变了）；Release `demo-20261101` 的 `demo_full_20261101.mp4`（59.5 MB）匿名可见 | ✅ |

---

## 三、工程包内容（指南 §3.3.5.1）

| 要求 | 在哪 | 状态 |
|---|---|---|
| 完整工程源码 | `src/` 三套实现；`board/overlay/build_*.tcl` 十份构建脚本 | ✅ |
| 仿真或验证结果，说明功能正确性如何确认 | `sim/run_all_checks.sh` 一条命令跑五项；四关判据见 `report/design_report.md` §5 | ✅ |
| 综合与实现报告，含资源占用、时钟频率、关键性能指标 | HLS 线：`build/hls/reports/`、`data/results/impl_metrics.md`；PL 直连线：`board/overlay/fir_pl_{timing,util}.rpt` | ✅ |
| 上板工程、运行脚本 | `board/overlay/*.bit` + `*.hwh` + `board/scripts/` + `board/probes/` | ✅ |
| 实测输出与参考结果的比对数据 | `data/results/` 全部原始输出；索引 `data/results/README.md` 标明哪份是当前结论、哪些不能引用 | ✅ |
| 优化前后的性能与资源对比（图） | 报告 §4.0 的取舍全貌图，由 `src/python/plot_pareto.py` **现场解析** 68 份 csynth 报告 + `impl_metrics.md` 生成，不手抄 | ✅ |
| 可复现的构建脚本 | `rebuild.sh`（csim / ip / bit 三段） | ✅ |

---

## 四、性能与资源（指南 §3.3.4）

| 要求 | 落实 | 状态 |
|---|---|---|
| 性能指标可测量，并与基线对比 | 同一颗芯片 ARM vs PL：16.7×；`data/results/accel_cpu_vs_fpga.md` 附四条必带说明（精度不同 / CPU 是 scipy C / 含 Python 开销 / 乘法对等 579 次） | ✅ |
| 不以显著资源代价换有限性能收益 | 限流对照实测：DSP 202→86 但 LUT 涨 2.6 倍、WNS 从 +1.624 崩到 −0.620，据此判定"不复用乘法器"；`report/design_report.md` §4.3 | ✅ |
| 资源使用率结合性能、时序与扩展余量判断 | 不再只是"承认余量薄"：§4.0 用非支配前沿说明**相邻格为什么被放弃**（少 DSP 必吃 LUT、更少拍必加 DSP），并注明该图的两个轴表达不了功能，比较只在同功能集内成立 | ✅ |
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
| 优化过程，含**优化前后的性能与资源对比表** | §4.0 取舍全貌图 + §4.1~4.5 四张过程表（抽头数 → 拍数轨迹 → DSP/资源 → 数据通路） |
| 大模型协作记录：提示词策略、模型输出与人工修正 | §6；§6.7 是**智能体工作流设计**（四步回路 + 人在回路位置） |
| 技能包提炼过程 | §7（含"从哪次失败抽出来的"对照表） |
| 复现说明 | §8；§8.9 是数据出处约定（数字 → 原始文件对照表） |

附加章节：§5.1 失真缺陷与四档预设（含"哪一档测过、哪一档没测"）、§8.4 失真复测命令、
§9 工具链与器件声明（含 PYNQ 版本对照表）、§10 已知边界（A 组影响结论 / B 组纯工程债）。

---

## 六、演示材料

| 项 | 现状 | 状态 |
|---|---|---|
| 演示通路 | `board/overlay/fir_pl.bit`（全 PL 直连）+ 网页验配，启动命令见根 `README.md`「当前演示版」 | ✅ |
| 人耳验收 | 2026-10-15 端到端 40 秒跑通，操作者确认听到处理后的声音；四段增益实时改动 6 次全部接住 | ✅ |
| 演示视频（全片） | [Release `demo-20261101`](https://github.com/JR0425/pynq-multiband-audio-accel/releases/tag/demo-20261101) 的 `demo_full_20261101.mp4`：59.5 MB / 1920×1080 / 2 分 59 秒，实机演示 + 验收过程；口径与报告 §3、§5 一致。**视频放 Release 不放 Git 历史**：59.5 MB 超过 GitHub 单文件建议体积，且历史里的大二进制删不掉 | ✅ |
| 四档运行期预设 | `demo` / `show` / `soft` / `clean`，一条 `POST /api/preset` 切换，不重编位流。每档带着"测没测过"的标注一起返回：`demo` 实测 **0.09%**（压缩器未启动，响度来自 MPO 限幅）、**`show` 实测 2.36%（压缩器确认动作，现场 A/B 用这一档）**、`clean` 实测 0.07%、`soft` 谐波 −90 dBc 量级（总数未单独记，返回 `None`） | ✅ |
| 现场音质兜底 | 以音质优先时切 `clean`；汇报时必须说明用的是哪一档。THD+N 复测命令写在报告 §8.4 | ✅ 11-02 已补测 |
| 决赛海报 | **不在本仓库范围内**：一页英文 A4、不含队伍信息（指南第六章），由队伍通过竞赛指定渠道单独提交。2026-11-02 起 `report/poster/` 已连同 Git 历史移除（`git rev-list --objects --all` 里没有任何 `*.pptx`；当前 HEAD 的 API 路径为 404）。**残留一处如实记录**：force push 之前的旧提交在 GitHub 服务端仍可按 commit SHA 取到，直到 GitHub 自己 GC 为止——要彻底清除得向 GitHub Support 提申请。也就是说这份 pptx 在 public 仓库上暴露过一段时间，若要当作未公开材料，应按已公开处理 | ➖ 外部交付 |

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
| 生成 `MANIFEST.txt` 放进包内 | 提交记录、每个文件的 sha256 与大小、md5 复核结果、演示视频是否收进去 |

边界与前提：

- **演示视频故意不入库，只从本机目录收**（`report/video/*.mp4`，59.5 MB，只在
  [Release `demo-20261101`](https://github.com/JR0425/pynq-multiband-audio-accel/releases/tag/demo-20261101)）。
  clone 下来这个目录里只有一份 `README.md`（写着从哪下载），脚本也**不会替你下载**。
  **视频是必带项：缺了脚本非 0 退出**（退出码 5），不再"警告后继续"—— 一份缺了实测演示的
  提交材料看起来是完整的，这是最坏的情况。
  确实不要视频才加 `--no-extras`（旧名 `--no-video` 仍可用）。
- **海报不在本仓库范围内**（队伍另行提交），打包脚本已不再处理它。
- **工作区有未提交改动时默认拒绝打包**（退出码 3），避免打出一份与提交记录对不上的材料；
  确认无误再加 `--allow-dirty`。
- 产物 `dist/` 已在 `.gitignore` 里，不会被自己打进去。
- **2026-11-02 晚核查：`dist/` 下现有三个包（`133e772` / `2099200` / `a3a7d0d`）逐个开箱检查，
  里面都没有 mp4** —— 三个都是 `--no-extras` 的产物，而本清单第六节写着"视频是必带项"。
  也就是说**现有任何一个包都不能直接拿去提交**。正式打包前必须先把
  `demo_full_20261101.mp4` 放回 `report/video/`（`report/video/*.mp4` 已在 `.gitignore` 里，
  放进去不会弄脏工作区，见 `.gitignore` 对应条目），再跑一次不带 `--no-extras` 的打包。
  同一次核查确认了退出码行为：缺视频时返回 **5**（拒绝出包），加 `--no-extras` 时返回 **0** 并复核两个关键工件的 md5。

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

# 3b. 失真口径自查：确认没有任何地方把 9.13% 写成"当前默认"
grep -rn "9.13" report/*.md README.md | grep -v "10-31\|冻结\|只对"

# 3c. 图能重画（现场解析 68 份报告，不手抄）
python src/python/plot_pareto.py && ls -l data/figures/report/fig4_1_pareto.png

# 3d. 四档预设自检（纯 Python，不需要板子）
python board/scripts/fir_pl_webui.py --selftest | tail -3

# 3e. 通用 PYNQ Skill 骨架自检（纯标准库，不需要板子也不需要 pynq）
python skill/templates/pynq_overlay_skeleton.py --selftest | tail -3

# 4. 断链自查：只查**带路径分隔符**的引用（裸文件名在正文里大量出现，查了全是噪声）
grep -rhoE '`[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+\.(md|py|tcl|sh|v|txt|png|bit|hwh)`' \
  report/*.md README.md BUILD_GUIDE.md | tr -d '`' | sort -u \
  | while read f; do [ -e "$f" ] || echo "缺: $f"; done
# 报告里插入的图片走相对路径，单独查一遍
grep -rhoE '\]\(\.\./[^)]+\)' report/*.md | sed 's/](//; s/)$//' \
  | sort -u | while read f; do [ -e "report/$f" ] || echo "缺图: report/$f"; done
```

第 3 条尤其要跑：**报告里的数必须能追到原始输出**，这是本项目一直守的一条 ——
`data/results/README.md` 就是为此存在的索引。
