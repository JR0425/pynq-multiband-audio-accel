# 提交清单 / 合规对照

> 对照《全国大学生嵌入式芯片与系统设计竞赛'2026 FPGA 创新设计赛道选题指南（AMD）》
> §3.3 自主选题赛道逐条核。
>
> 状态标记：✅ 完成 ｜ ⚠️ 有缺口、已写明边界 ｜ ⬜ 待办（阻塞提交，必须先处理）
> 最后核对：**2026-11-02 夜（第三轮：提交前完整性核查）**（上两版：2026-11-02 夜、2026-11-02 晚）
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
> **11-02 夜这一轮改了什么**（对着指南复核第二轮，重点是"做了但没写"和"写错了对象"）：
> 14. **新增 `report/fir_pl_register_map.md`** —— 演示通路 `fir_pl.bit` 的 32 个寄存器契约。
>    原来 §2.4 把"完整寄存器级约定"指向 `hardware_interface_spec.md`，而那份写的是**上一代块模式
>    HLS 核**（`AP_CTRL`/`in_r`/`out_r`），两套地址没有一处重合；照它写驱动对着演示位流一个字都读不对。
>    现在两份并存并互相注明适用位流，§2.4 按通路分别指向。
> 15. §9.3 与"这三句话不矛盾"段：把"Tcl 全流程重跑，**产物一致**"改成"**资源与时序在同一档**"。
>    原写法与 `clean_build_accept_20261102.md` 自记的 MD5 不同直接矛盾（`534a4b60` 对 `628b75b3`）。
> 16. 新增 **§2.5 时钟方案与时序约束**、**§2.6 I2S 接口协议**、**§2.7 两个状态机**。
>    初级组的考察侧重是"逻辑设计、状态机、时序约束、接口协议"，而这三样原报告一个字没写 ——
>    不是没做（`i2s_duplex.v` 里 6 态收 + 4 态发都是真的），是没写进报告。本轮不涉及任何新实验。
> 17. 报告插图从 **1 张补到 14 张**，把 `data/figures/report/` 下现成的 `fig*.png` 全部按章节插入
>    （含最该在的系统框图 `fig2_1_block.png`）。全部由 `plot_report_figures.py` / `plot_pareto.py` 生成。
> 18. **`BUILD_GUIDE.md` → `build_guide.md`**（10 处引用同步）。指南 §3.3.5.4 要求小写，
>    只有示例里的 `README.md` 与惯例名 `LICENSE` 属例外，原大写名严格读不合规。
> 19. 新增 `board/notebooks/README.md`：四个 notebook 全部指向上一代 `fir.bit`，初级组推荐 PYNQ/Jupyter，
>    评委很可能直接点开就走错通路 —— 该文件说明每个 notebook 对应哪颗位流、演示该用哪个入口。
>
> **11-02 夜第三轮：提交前完整性核查，改的全是"照文档做会卡住"的地方**
> 20. 补 **`board/scripts/pl_supervise.sh` + `fir-pl-webui.service` + `install_pl_webui_service.sh`**。
>    README 让评委 `systemctl start fir-pl-webui`，而这两个文件**从未入过库**
>    （`git log --all -- board/scripts/pl_supervise.sh` 为空），照着做只会拿到 unit not found。
>    仓库里原来只有上一代 `fir-webui.service`（指向 `fir_webui.py`）和一次性的 `pl_webui_up.sh`。
> 21. **160× 补进 README「30 秒」表、报告 §0 / §3 通路表 / §8.9 / 附录、本节 §四。**
>    原来这个数只活在 `data/results/` 里，三份主文档都只有 16.7× —— 而 16.7× 恰恰被自己
>    标注"不是演示通路"，等于读者在入口处看不到演示通路最强的那条证据。
> 22. 位流数量口径：README 与报告 §0 的"20 多颗"改成**入库 11 颗**（`git ls-files "*.bit"`，可当场验）；
>    §八 加一条断言，多一颗少一颗都会在自检里露头。
> 23. `data/results/accel_demo_chain_20261102.md` 里两处 `build/overlay/` 改成 `board/overlay/`
>    （`build/overlay/` 这个目录不存在，照着找会扑空）。
> 24. `dist/` 里的旧包整体移入 `dist/_stale/`；本节 §七 不再写死包名，判据改成
>    "包名里的 short sha 必须等于 `git rev-parse --short HEAD`" —— 上一版写死了四个包名，
>    八分钟后就因为又打了一个包而过期。
> 25. `.gitattributes` 补 `*.sh` / `*.service` 为 `eol=lf`：本机 `autocrlf=true`，clone 到
>    Windows 会把这些脚本写成 CRLF，拷上板就是 `/bin/sh^M`。
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
> 7. **海报从仓库（含 Git 历史）整体移除**，改由队伍按竞赛渠道单独提交。这条前后清了**三遍**：
>    第一遍之后另一处会话把一个仍留着旧历史的本地克隆合并回 `main`，pptx 又跟着 merge 回到了
>    历史里；第二遍重新 `filter-repo` 后强推，但**当时写的"`git rev-list --objects --all` 已无
>    `*.pptx`"是错的** —— 11-02 夜提交前核查实测 `git rev-list --objects HEAD` 里仍有
>    `report/poster/poster_cn.pptx`（2.00 MB）与 `poster_en.pptx`（1.93 MB），`PK` 头完整、
>    可整份解出，说明 merge 又一次把旧历史带了回来；第三遍重新 `filter-repo` 后两条命令才真为空
>    （提交数 309 → 281，减少的 28 个是同一标题成对出现的重复提交各并成一条，文件树 996 个文件
>    逐字节不变，`git diff` 为空）。
>    **复发机制没变**：只要别的克隆还留着旧历史，它一 merge 就会把 pptx 带回来。清完历史之后，
>    其余克隆必须 `git fetch --prune && git reset --hard origin/main`，**不许从旧分支 merge**。

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
| 目录与文件名：小写字母、数字、下划线或连字符 | 目录全小写、无中文、无空格；文件名全小写，仅 `README.md` 与 `LICENSE` 两个常规大写名（指南 §3.3.5.4 的示例里含 `README.md`，`LICENSE` 为跨平台惯例）。**`BUILD_GUIDE.md` 已于 11-02 改为 `build_guide.md`** —— 指南的"小写"要求里没有这个例外，原写法严格读不合规 | ✅ |

---

## 二、工具链与板卡声明（指南 §3.3.3.1 / §3.3.3.2）

| 要求 | 落实情况 | 状态 |
|---|---|---|
| **声明参赛组别** | **初级组**（报告首页与本清单一致）；器件 `xc7z020clg400-1` 在初级组允许范围内 | ✅ |
| 声明具体板卡型号，需在指定板卡上实际跑通 | PYNQ-Z2，`xc7z020clg400-1`；全部数据来自板上实测，非仿真评估 | ✅ |
| Vivado/Vitis 2026.1（推荐）或 2025.2 | **实际用 2020.2**，但论证换过了：**PYNQ 镜像与 Vivado 成对发版，PYNQ-Z2 镜像停在 3.1.0（2024.1），4.0.0（2025.2）已移除 Z2 支持** —— "2025.2 + PYNQ-Z2"在官方栈里不存在。2024.1 存在但未采用的三条理由单列。全文 `report/design_report.md` §9.1 / §9.2 | ⚠️ 已说明（可查证） |
| 使用其他版本需在报告中说明并保证脚本可复现 | `rebuild.sh` + `build_guide.md`：三步全 Tcl 化，工具位置用 `VIVADO_BIN` / `VITIS_HLS_BIN` 环境变量给 | ✅ |
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
| 性能指标可测量，并与基线对比 | 同一颗芯片 ARM vs PL 两个数并报：**演示通路 160×**（`data/results/accel_demo_chain_20261102.md`，手写 RTL 核 3 拍，四次连跑 158.9—161.3）与**上一代 16.7×**（`data/results/accel_cpu_vs_fpga.md`，HLS 核 36 拍、块模式位流）。两个数不能替代也不能相乘；16.7× 附四条必带说明（精度不同 / CPU 是 scipy C / 含 Python 开销 / 乘法对等 579 次），160× 另带"用同源 RTL 的 DMA 外壳计时、对演示通路偏保守" | ✅ |
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
| 演示通路 | `board/overlay/fir_pl.bit`（全 PL 直连）+ 网页验配，启动命令见根 `README.md`「当前演示版」。服务本体已入库：`board/scripts/pl_supervise.sh` + `fir-pl-webui.service` + `install_pl_webui_service.sh`，`sudo sh install_pl_webui_service.sh` 一次装好，之后 `systemctl start/stop fir-pl-webui` | ✅ |
| 人耳验收 | 2026-10-15 端到端 40 秒跑通，操作者确认听到处理后的声音；四段增益实时改动 6 次全部接住 | ✅ |
| 演示视频（全片） | [Release `demo-20261101`](https://github.com/JR0425/pynq-multiband-audio-accel/releases/tag/demo-20261101) 的 `demo_full_20261101.mp4`：59.5 MB / 1920×1080 / 2 分 59 秒，实机演示 + 验收过程；口径与报告 §3、§5 一致。**视频放 Release 不放 Git 历史**：59.5 MB 超过 GitHub 单文件建议体积，且历史里的大二进制删不掉。**11-02 夜定：视频经竞赛指定渠道单独提交，不进本仓库的 zip**（打包带 `--no-extras`） | ➖ 外部交付 |
| 四档运行期预设 | `demo` / `show` / `soft` / `clean`，一条 `POST /api/preset` 切换，不重编位流。每档带着"测没测过"的标注一起返回：`demo` 实测 **0.09%**（压缩器未启动，响度来自 MPO 限幅）、**`show` 实测 2.36%（压缩器确认动作，现场 A/B 用这一档）**、`clean` 实测 0.07%、`soft` 谐波 −90 dBc 量级（总数未单独记，返回 `None`） | ✅ |
| 现场音质兜底 | 以音质优先时切 `clean`；汇报时必须说明用的是哪一档。THD+N 复测命令写在报告 §8.4 | ✅ 11-02 已补测 |
| 决赛海报 | **不在本仓库范围内**：一页英文 A4、不含队伍信息（指南第六章），由队伍通过竞赛指定渠道单独提交。11-02 夜第三遍清理后实测：`git rev-list --objects --all` 的输出里已无任何 `*.pptx`、`git log --all -- report/poster` 无输出、当前 HEAD 的 API 路径为 404。**残留一处如实记录**：force push 之前的旧提交在 GitHub 服务端仍可按 commit SHA 取到，直到 GitHub 自己 GC 为止——要彻底清除得向 GitHub Support 提申请。也就是说这份 pptx 在 public 仓库上暴露过一段时间，若要当作未公开材料，应按已公开处理 | ➖ 外部交付 |

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
  缺件时脚本非 0 退出（退出码 5），不"警告后继续"—— 一份缺了实测演示的提交材料看起来是完整的，
  这是最坏的情况。
- **本仓库的提交包不收视频（11-02 夜定的）**：视频改由队伍经竞赛指定渠道单独提交，
  所以本仓库打包一律带 `--no-extras`（旧名 `--no-video` 仍可用）。
  也就是说第六节"演示视频"那一行是**外部交付**，不在 zip 里；zip 缺 mp4 不再是缺陷。
  脚本保留退出码 5 的硬判据不变 —— 它是防"以为带了其实没带"的，与本决定不冲突。
- **海报不在本仓库范围内**（队伍另行提交），打包脚本已不再处理它。
- **工作区有未提交改动时默认拒绝打包**（退出码 3），避免打出一份与提交记录对不上的材料；
  确认无误再加 `--allow-dirty`。
- 产物 `dist/` 已在 `.gitignore` 里，不会被自己打进去。
- **提交前必须按当前 HEAD 重打一次，不要把旧包拿去交。** 判据只有一条：包名里的 short sha
  必须等于 `git rev-parse --short HEAD`（`pynq-multiband-audio-accel_<short-sha>_<日期>.zip`），
  对不上就是上一版留下的。
  2026-11-02 夜第三次核查时，`dist/` 下的旧包**全部落后于 HEAD**，其中三个包对应的 commit
  已在历史重写中消失（`133e772`、`14e0dbb`、`a3a7d0d`），`7006c5d` 只剩一个 zip、连解包目录都没有；
  这几份已整体移入 `dist/_stale/`，不再参与提交。**这个目录名不写死在文档里** ——
  写死过一次（原来这行列了"四个包"和它们各自的 commit），八分钟后就因为又打了一个包而过期。
- 退出码行为（2026-11-02 实测，不是推断）：缺视频时返回 **5**（拒绝出包），
  加 `--no-extras` 时返回 **0** 并复核两个关键工件的 md5，工作区脏时返回 **3**。

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

# 3f. 演示启动链的三个文件必须在：README 让评委直接 systemctl start fir-pl-webui，
#     少一个这条命令就报 unit not found（2026-11-02 之前就是缺的）
ls board/scripts/pl_supervise.sh board/scripts/fir-pl-webui.service \
   board/scripts/install_pl_webui_service.sh

# 3g. 位流数量口径：README 与报告 §0 都写"入库 11 颗"，多一颗少一颗都要回来改文档
test "$(git ls-files '*.bit' | wc -l)" -eq 11 && echo "11 颗，与文档一致"

# 4. 断链自查：只查**带路径分隔符**的引用（裸文件名在正文里大量出现，查了全是噪声）
grep -rhoE '`[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+\.(md|py|tcl|sh|v|txt|png|bit|hwh)`' \
  report/*.md README.md build_guide.md | tr -d '`' | sort -u \
  | while read f; do [ -e "$f" ] || echo "缺: $f"; done
# 报告里插入的图片走相对路径，单独查一遍
grep -rhoE '\]\(\.\./[^)]+\)' report/*.md | sed 's/](//; s/)$//' \
  | sort -u | while read f; do [ -e "report/$f" ] || echo "缺图: report/$f"; done
```

第 3 条尤其要跑：**报告里的数必须能追到原始输出**，这是本项目一直守的一条 ——
`data/results/README.md` 就是为此存在的索引。
