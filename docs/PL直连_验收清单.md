# PL 直连 · 验收清单（2026-10-29 待办，照顺序做）

> 每一项都写了"命令 + 期望输出"。没写期望的项不要算过。

## A. 不需要板子（现在就能跑）

```bash
bash sim/run_all_checks.sh
```

期望最后一行：`全部通过。板上那两条另算...`，五项都是 `OK`。

| 项 | 覆盖什么 |
|---|---|
| 1/5 三组 DRC 仿真 | 与改动前 RTL 逐位对拍、四参数 vs HLS 公式（含 7 组参数扫描）、增益斜坡 |
| 2/5 寄存器单测 | 出厂值、写读回、四段参数各自落到自己槽位、CTRL 自清/脉冲 |
| 3/5 RTL 语法 | 六个 RTL 文件 xvlog 无 error/warning |
| 4/5 后端自检 | 换算、写寄存器序列、status 字段、观察量换算、run 开关 |
| 5/5 网页自检 | 24 项 HTTP 接口与范围钳制 |

## B. 上板（需要板子 + 耳机，白天做）

**懒人版（推荐）**：一条命令把 B1/B2/B3/B3b 全跑完，最后打一张 PASS/FAIL 表。

```bash
bash tools/board_debug/board_accept.sh              # 会从耳机放 3 秒 440 Hz
bash tools/board_debug/board_accept.sh --quiet      # 只验寄存器，不出声
bash tools/board_debug/board_accept.sh --skip-push  # 位流已经是这版就不重推
```

下面 B1 起是分步的细节，出问题时照着单独跑。


### B1 推位流

```bash
cd tools/board_debug
MSYS_NO_PATHCONV=1 python board_ssh.py --put ../../board/overlay/fir_pl.bit /home/xilinx/dma_exp/fir_pl.bit
MSYS_NO_PATHCONV=1 python board_ssh.py --put ../../board/overlay/fir_pl.hwh /home/xilinx/dma_exp/fir_pl.hwh
```

期望：两行都以 `md5 ... ok` 结尾。**bit 和 hwh 必须同一次构建**。

### B2 寄存器窗口

```bash
MSYS_NO_PATHCONV=1 python board_ssh.py --sudo \
  "cd /home/xilinx/dma_exp && XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u pl_regs_check.py"
```

期望：`[2]` ID 0x504C0002、`[4b]` 四参数出厂值、`[4c]` 四段写读、`[5]` busy 落零，
最后 `REGS PASS`。

> 2026-10-28 已在批 2 定版位流上跑过一遍，全过。

### B3 压缩器随参数动（两条，任选）

```bash
# ① 话筒当源。安静房间量不出东西时会报 PASS(partial)，不算失败
... pl_drc_probe.py

# ② 播放环里填 440 Hz 正弦当源（会出声，白天跑）。
#    16384 采样正好 150 周期，回绕不咔哒；不需要话筒也不需要耳朵
... pl_drc_ply_probe.py
```

期望（②）：`[3]` 出厂参数下 GR > 0 且 `GR/(ENV−THR) ≈ 0.7`；
`[4]` THR 抬到 2.0 FS 后 GR 全 0；`[5]` ATK=REL=0 时仍然压；最后 `DRC PLY PROBE PASS`。

**这是"四参数真的进了数据通路"唯一没拿到的板上证据，明天第一件补它。**

### B3b 双耳两套处方（2026-10-29 新增）

```bash
... pl_dual_probe.py
```

期望：`[2]` 两套参数相同时左右 ENV/GR 逐段相同（差 ≤ 1 LSB）；
`[4]` 只压右耳门限后 `max(grR) > max(grL)`；`[6]` 只抬右耳 +18 dB 后
`envR/envL ≈ 8`；最后 `DUAL PROBE PASS`。

三条判据分别证伪：右耳没在算、参数串台、两套增益共用一个斜坡。

### B4 网页第三档

```bash
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_live.py &
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_webui.py --port 8080
```

浏览器开 `http://192.168.2.99:8080/`。逐项看：

| 看什么 | 期望 |
|---|---|
| 页首"PL 直连 · 观察量"面板 | 有 PEAK IN → PEAK OUT、四段 ENV、四段 GR 三行数字 |
| 块长/来源/录音三个控件 | 不见了（PL 版不适用） |
| 四个滑杆 | 上限是 +42，不是 +24 |
| 每段"听"按钮 | 点了其余三段掉到 −24 dB；再点恢复 |
| 拖任意滑杆 | 板子上有反应（听感或 GR 读数变化），不报错 |
| 页脚提示 | 没有"status 超过 3 秒没更新"（有就是 fir_pl_live 没起来） |

### B5 听音（需要你）

| 项 | 怎么判 |
|---|---|
| 说话 +30 dB | 明显变响、不啸叫；回到 0 dB 应该几乎听不见（话筒很轻） |
| 四段分别"听" | 四段的音色明显不同：①低频厚 ②人声主体 ③辅音清脆 ④齿音 |
| 拖增益滑杆 | 平滑爬升，没有"咔"（斜坡 RAMP_DIV=5） |
| 勾"直通" | 只剩四段增益，压缩不动作（音色更冲、峰值更高） |

> 插耳机那个坑：板载耳机座只在插入的上升沿检测一次，**要反复插拔到电平跳起来**
> 再开始测，之后全程别碰插头。

## C. 演示前 5 分钟

1. 确认没有第二个 overlay 进程：`ps -ef | grep -E "fir_pl_live|ply_upload"`；
2. 确认没在放音：`r.write(0x024, 0)` 或直接 `ply_upload.py --stop`；
3. 网页刷新一次，面板有数、滑杆位置对（+30 dB）；
4. 讲数字前看 `docs/PL直连_资源与时序.md` §5 的口径（乘法次数、2083 拍、PS 在环与否分开讲）。

## D. 明确还没做的（别在演示里承诺）

- 波形/频谱、录音下载：抓取缓冲（C3/C4）没做，RTL 里是桩。
- 听力测试建档：要按频率放音，PL 直连没有这条路。
- 双耳各调各的：bank1 现在写的是 bank0 的镜像；DSP 95%，直接例化两份超资源，
  走时分复用（见 `docs/PL直连_资源与时序.md` §4）。
- MPO 限幅（B6）、THD+N（C6）、600 秒长跑（E3）：都没做。
- 网页还**没有**两套处方的滑杆界面（现在只能后端写 `tune["b1"]`）；
  演示双耳请用 `pl_dual_probe.py` 或手写 json，别说"网页上能分开调"。
- 板子放几分钟 WAV 可能整机挂（历史现象，未定位）。
