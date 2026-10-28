# PL 直连 · 网页第三档（`fir_pl_webui.py`）

> 2026-10-28 深夜。第一/二档是 `fir_webui.py`（PS 在环，块模式）；这一档只服务 PL 直连。

## 1. 为什么单独一个进程

第一档的 `fir_webui.py` 干的事很多：拉 `fir_live.py`、管块长/延迟档、录 wav、跑听力测试。
这些在 PL 直连里全部不存在——音频在 PL 里一进一出，PS 不碰样本。

所以第三档只做一件事：**把页面的 HTTP 请求翻译成两个 json 文件**。

```
浏览器 ──HTTP──> fir_pl_webui.py ──写──> /dev/shm/fir_tune.json
                                        ↑ 读
                                  fir_pl_live.py（持有 overlay，写寄存器）
                                        ↓ 写
                                 /dev/shm/fir_status.json ──读──> 页面
                                        ↑
                              /dev/shm/fir_pl_run.txt（run 开关）
```

为什么 `run` 用文件而不是网页进程直接写 MMIO：**overlay 只能在一个进程里加载**。
两个进程各自 `Overlay()` 会把 PL 重配一遍，先加载的那个进程的映射就废了。

## 2. 接口（`page.html` 在用的那 7 个）

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/` | 返回 `page.html`（和第一档同一个文件） |
| GET | `/api/status` | 读 `fir_status.json`，附 `age` / `stale` / `mode:"pl"`；文件不存在返回 `{"ok":false,"running":false}` |
| GET | `/api/tune` | 读 `fir_tune.json`，没有则 `null`（页面会填默认值并写回） |
| POST | `/api/tune` | 范围钳制后写 `fir_tune.json`，回显钳制结果 |
| GET | `/api/scope` | `{"ok":false, "msg":"PL 直连版没有波形缓冲（C3/C4 未实现）"}` |
| GET | `/api/health` | 位流存在/大小、status 新鲜度、run 标志、路径 |
| POST | `/api/start` | 写 `run=1`，并告诉页面后端进程到底在不在 |
| POST | `/api/stop` | 写 `run=0` |

钳制范围与第一档逐条一致：gain −24..+42、thr −60..0 dBFS、ratio 0..0.95、
attack 0..50 ms、release 0..500 ms。换算（dB→Q7.8、ms→Q2.22）**不在网页侧做**，
仍然由 `fir_pl_live.apply_tune()` 一处负责，避免两套换算慢慢漂开。

`/api/start` 收到的 `src / l / nbuf / wav` 全部忽略：PL 直连没有块长和来源可选。
`/api/rec`、`/api/hearing/tone` 明确返回 `ok:false` 并说明原因，不假装成功。

## 3. 页面的增量（`page.html` 末尾那一段）

全部塞在一个 `(function(){...})()` 里，触发条件只有一个：`/api/status` 的
`mode === "pl"`。第一/二档跑起来这段什么都不做，界面一个字都不动。

| 增量 | 说明 |
|---|---|
| 观察量面板 | 页首插入：`PEAK IN → PEAK OUT`、四段 ENV、四段 GR。ENV/GR/PEAK 都是 PL 里的真信号（`tb` 对过，板上读过） |
| 单段试听（`听` 按钮） | 其余三段拉到 **−24 dB**。**PL 版没有独立静音位**，所以这不是真静音，只是把别的段压到最小 —— 按钮 tooltip 里写清楚了 |
| 滑杆上限 | 第一/二档 ±24 dB；PL 档改到 +42 dB（演示口径 +30） |
| 隐藏控件 | 块长、来源、录音勾选 —— 这三样只在 PS 在环时有意义 |

`in_rms / out_rms` 那两根电平条仍然是空的：`fir_pl_regs` 里的均方累加是桩
（`build_audio_pl.tcl` 里被接到常量 0），等 C3/C4 做完再补。面板脚注写了这句，
免得演示时被当成"没声音"。

## 4. 怎么跑

```bash
# 板上，两个进程
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_live.py &
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_webui.py --port 8080

# 或者用包装脚本（只起网页，后端要自己先起）
bash /home/xilinx/dma_exp/run_pl_webui.sh 8080
```

浏览器开 `http://192.168.2.99:8080/`。

## 5. 自检

```bash
python3 fir_pl_webui.py --selftest     # PC 上就能跑，不加载 overlay
```

24 项，覆盖：页面可取、无后端时的降级、五组范围钳制、短列表补齐、
start/stop 真的改 `run` 文件、status 透出观察量、没实现的接口明确拒绝、未知路径 404。
临时文件写在 `tempfile.mkdtemp()` 里，跑完不留垃圾。

## 6. 没做的

- 波形/频谱：要抓取缓冲（C3/C4），RTL 里现在是桩。
- 录音下载：同上。
- 听力测试建档：要按频率放音，PL 直连没有这条路（WAV 播放是整段播）。
- 双耳各调各的：现在 bank1 写的是 bank0 的镜像（`fir_pl_live.apply_tune` 里双写）。
  要做独立双耳，得先把 DRC4 例化两份（任务 4）。
