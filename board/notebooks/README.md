# `board/notebooks/` —— 先说清楚每份笔记本对应哪颗位流

> **演示通路（`fir_pl.bit`）不用这里的任何一份笔记本。**
> 全 PL 直连版的入口是 `board/scripts/fir_pl_live.py`（100 Hz 控制环）、
> `board/scripts/fir_pl_webui.py`（网页验配，:8080）和 `board/probes/board_accept.sh`（上板验收）。
>
> 这个目录里的四份笔记本是**上一代块模式通路**（`fir.bit` / `base.bit`）的调试与教学产物，
> 留着是因为它们记录了"为什么要做这件事"和"那些坑是怎么发现的"。

## 逐份说明

| 笔记本 | 配的位流 | 它证明什么 | 现在还跑得通吗 |
|---|---|---|---|
| `w2_audio_playback.ipynb` | PYNQ 出厂 `base.bit` | **动机来源**：play 3 秒墙钟 2.96 s / CPU 2.96 s，等于一个核被 100% 占满 —— 采样是 CPU 一个数字一个数字搬的 | 能（只依赖 PYNQ 镜像） |
| `w3_own_overlay_audio.ipynb` | 自建 `audio.bit` | 自建 overlay 也能出声：证明"把音频接进自己的 PL 设计"这条路成立 | 能（需先出 `audio.bit`） |
| `test_multiband.ipynb` | **`board/overlay/fir.bit`** | **上一代 HLS 核**的板级自检：bypass 模式下输出必须逐位等于输入延迟 96 拍 | 能（需 `fir.bit` + `fir_core.py`） |
| `w5_tuning_ui.ipynb` | **`board/overlay/fir.bit`** | 四段增益 + 压缩四参数的实时验配界面，配合 `fir_live.py` 那个进程 | 能（需 `fir.bit` + `fir_live.py`） |

后两份跑的是**块模式 HLS 核**，寄存器是 HLS 自动生成的 `AP_CTRL` / `in_r` / `out_r` 那一套
（`report/hardware_interface_spec.md`）。**它不是演示位流** ——
`fir_pl.bit` 的寄存器由 `src/rtl/fir_pl_regs.v` 手工排布，两套地址没有一处重合
（`report/fir_pl_register_map.md`）。

## 想验演示通路，跑这三条

```bash
# 无板自检（主机侧）
python board/scripts/fir_pl_live.py --selftest
python board/scripts/fir_pl_webui.py --selftest

# 上板验收（寄存器窗口 / 压缩器 / 双耳处方 / 抓取窗口，逐项打 PASS）
bash board/probes/board_accept.sh

# 长跑（每 2 秒读一次状态，按 12 条判据打 PASS）
python board/probes/soak_pl_600.py --secs 600 --json soak_pl.json
```

为什么演示通路没做 notebook：它需要一条常驻的 100 Hz 控制环（`fir_pl_live.py`）和一路
HTTP 服务（`fir_pl_webui.py`），跑在 systemd 下、与 `bootpy.service` 有加载顺序依赖
（见设计报告 §8.5）。把它塞进 Jupyter 内核里，控制环会跟着内核一起被阻塞 ——
这正是 `w5` 那份笔记本里要开后台线程、还要小心 `SCHED_FIFO` 抢核的原因。
