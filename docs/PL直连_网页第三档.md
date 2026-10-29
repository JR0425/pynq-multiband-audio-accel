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
| POST | `/api/tune` | 范围钳制后写 `fir_tune.json`，回显钳制结果。可选 `b1` = 右耳处方（见 §2.1） |
| GET | `/api/scope` | `{"ok":false, "msg":"PL 直连版没有波形缓冲（C3/C4 未实现）"}` |
| GET | `/api/health` | 位流存在/大小、status 新鲜度、run 标志、路径 |
| POST | `/api/start` | 写 `run=1`，并告诉页面后端进程到底在不在 |
| POST | `/api/stop` | 写 `run=0` |

钳制范围与第一档逐条一致：gain −24..+42、thr −60..0 dBFS、ratio 0..0.95、
attack 0..50 ms、release 0..500 ms。换算（dB→Q7.8、ms→Q2.22）**不在网页侧做**，
仍然由 `fir_pl_live.apply_tune()` 一处负责，避免两套换算慢慢漂开。

`/api/start` 收到的 `src / l / nbuf / wav` 全部忽略：PL 直连没有块长和来源可选。

### 2.1 右耳处方 `b1`（2026-10-29）

```json
{ "gain_db": [30,30,30,30], "thr_db": [-20,-20,-20,-20], "ratio": [0.7,0.7,0.7,0.7],
  "attack_ms": [5,5,5,5], "release_ms": [100,100,100,100],
  "b1": { "gain_db": [0, 6, 12, 18] } }
```

- **没有 `b1`** = 两只耳朵一样：`fir_pl_live.apply_tune()` 把 bank0 的五个参数
  原样写进 bank1。这是默认，也是演示时的安全状态。
- **有 `b1`** = 各写各的。`b1` 里缺哪项就用左耳那项顶上（只给 `gain_db` 时，
  右耳的门限/压缩比/时间常数跟左耳一致），范围钳制和左耳同一套。
- 网页上对应 PL 面板里的「双耳（右耳独立处方）」开关 + 右耳四条增益滑杆。
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
- 双耳**只有增益**能各调各的（网页上四条右耳滑杆）。右耳的门限/压缩比/时间常数
  目前只能通过 `b1` 直接写 json，还没做滑杆。
- `EAR_CTL[0]` 只是一个软件约定，RTL 里两份 DRC 一直在跑、没有硬件门控。

## 0. 端口冲突：8080 开机被第一档占着（2026-10-29）

板子上有个 systemd 服务 `fir-webui.service`，**每次开机都会把第一档**
（`/home/xilinx/jupyter_notebooks/fir_webui.py`，PS 在环那版）拉到 8080。
所以重启完直接开 `http://192.168.2.99:8080/` 看到的是**第一档页面**，
没有 PL 观察量面板 —— 不是第三档没生效。

```bash
sudo systemctl stop fir-webui        # 腾出 8080（重启后会自己回来）
sudo sh /home/xilinx/dma_exp/pl_webui_up.sh   # 起第三档：live → webui
```

想让它永久优先第三档：`sudo systemctl disable fir-webui`。

判据：`curl :8080/api/tune` 必须返回 `{"ok": true, "tune": {...}}`
（第一档也返回这个结构，所以再看 `/api/status` 里有没有 `env_r` / `gr_r` / `dual`；
有就是第三档）。

### 另一个坑：旧页面会把 0 dB 写回板子

页面初始化是 `if (r.ok && r.tune) applyTune(r.tune); else flushTune()`。
第一档/缓存的旧页面拿到 `tune: null`（或自己的默认值）就会**把增益 0 dB、
门限 −10 dBFS 写进 /dev/shm/fir_tune.json**，于是"网页正常但听不见"。
第三档后端已经改成永远返回 `{"ok":true,"tune":演示默认}`，
但**换版本后仍要在浏览器里 Ctrl+F5 强刷一次**，否则跑的是缓存里的旧 JS。
