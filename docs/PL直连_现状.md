# 全 PL 直连版：现状、怎么验、下一步

> 最后更新：2026-10-28 ｜ 对应位流 `board/overlay/fir_pl.bit`（md5 `4f15ea6f…`，
> 板上实测的那颗是 `ccd0a6cf…`，同一份 RTL）
> 配套：`docs/PL直连_寄存器表.md`（寄存器映射）、`skill/pitfalls/pl_internal_tone_diagnosis.md`

## 一句话

**整条链在硬件上已经通了**：PL 内音源和话筒两条路都能在耳机里听到。
现在缺的不是"能不能出声"，而是"能不能在运行期调参数"（网页要驱动它）。

## 数据通路

```
话筒 ──ADC──┐                                        ┌──DAC── 耳机
            │                                        │
        i2s_duplex(收) → 录音FIFO → fir_mb_fe → fir_drc4 → 输出FIFO → i2s_duplex(发)
                          (每帧1个采样)   (四段分频)  (压缩)             (每帧2个字)
                                                                 ↑
                                                        ctrl[3] ? 片内440方波
```

- 前端每帧只吃**一个**采样（L），处理后写**两份**字（L/R）——2026-10-27 的修正，
  `sim/rtl_sim/tb_pl_channel.v` 验过 `fed/frames=1.000`、`ply/frames≈2.000`
- 相位累加器挂在 `out_vld` 上，所以测试音频率严格按帧率算

## 实测读数（2026-10-28，板上）

| 项 | 值 |
|---|---|
| `ctrl=0x0B`（run\|drc\|tone） | 耳机听到 440「嘟——」 ✅ |
| `ctrl=0x03`（run\|drc）+ 对话筒吹气 | 耳机有声 ✅ |
| `und` / `ovf` / `oovf` | 0 / 0 / 0 |
| 喂入 = 吐出 | 48937 /s ≈ 48.83 k（每帧 1 个采样） |
| codec | `0x4014=20 0x4019=33 0x401C=21 0x401E=41 0x4023=F3 0x4029=03` |
| 时序 | WNS **+0.222 ns** / WHS **+0.024 ns**，0 失败端点 |
| 资源 | DSP 197 / 220 |

## 怎么验（一条命令）

```bash
# 1) 推到板子（--putb64 走 base64，避开 Windows 路径转换）
MSYS_NO_PATHCONV=1 python tools/board_debug/board_ssh.py \
    --putb64 board/overlay/fir_pl.bit /home/xilinx/dma_exp/fir_pl.bit
MSYS_NO_PATHCONV=1 python tools/board_debug/board_ssh.py \
    --putb64 board/overlay/fir_pl.hwh /home/xilinx/dma_exp/fir_pl.hwh

# 2) 跑完整验收（5 秒测试音 → 静音 1 秒 → 10 秒话筒）
MSYS_NO_PATHCONV=1 python tools/board_debug/board_ssh.py --sudo \
  "cd /home/xilinx/dma_exp && XILINX_XRT=/usr \
   /usr/local/share/pynq-venv/bin/python3 -u pl_demo_check.py"
```

判读：**第 1 段听到「嘟」⇒ 播放链路全通；第 2 段吹气有声 ⇒ 话筒链路全通。**
两段都成立才算过。

`pl_demo_check.py` 里的顺序不能改：

```
加载 overlay → 读寄存器（出厂值）→ codec_init() → 再读（0x4023 应为 F3）
→ 确认 bit1=1 → 放音
```

原因见 `skill/pitfalls/pynq_codec_i2c.md`：**codec 的地址脚由 PL 驱动，不加载 overlay 就读不到**，
读回全是常数（`0x00`/`0x01`）时不要以为是配置错了。

## 控制位（现在走 `gpio_ctrl`，32 位）

| 位 | 含义 |
|---|---|
| `[0]` | `run` 放行 |
| `[1]` | `drc_en` 1=走压缩，0=只过前端（旁路） |
| `[2]` | `flush` 清两侧 FIFO + 前端 |
| `[3]` | `tone_en` 片内 440 方波顶替输出（**不过增益级**） |
| `[8]` | 清计数器（单拍） |

调试口：`gpio_dbg` @ `0x43C30000`（`dbg0` = 喂入/吐出字数，`dbg1` 位序见
`board/scripts/pl_direct_probe.py` 的注释）。

## 下一步：把 DRC 参数端口化（**没做完**）

四段增益不用新写数据通路 —— `fir_drc4_chan` 里已经有 `GAIN`
（`gmul = p_band * GAIN`，`src/rtl/fir_drc4.v:134`），THR/RATIO/ATK/REL 也都是参数。
所以「运行期可调」= **把参数改成端口**。

三处会挡路的本地参数：

| 位置 | 现在 | 要变成 |
|---|---|---|
| `localparam USE_WDRC = ((ATK!=0)||(REL!=0))` (v:106) | 编译期选通路 | 运行期按 `atk==0&&rel==0` 选，两条路都保留 |
| `localparam THR24 = THR` (v:109) | 常量 | 信号，比较跟着重定时 |
| `localparam OMR24 = (1<<FRAC_DRC) - RATIO` (v:110) | 常量 | 信号，多一个 24 位减法 |

建议顺序（每步都能单独验）：

1. **先只端口化 `GAIN`**（4 个 16 位输入，Q7.8，256 = 0 dB）。它只出现在 v:134
   一处乘法、不参与任何 localparam，改动最小。斜坡放 `fir_pl_direct` 里做
   （`gc += (tgt-gc)>>>6`，约 1.3 ms 收敛），不要在 DRC 内部改状态。
2. 再端口化 `THR/RATIO/ATK/REL`，此时必须处理 `USE_WDRC` ——
   `ATK=REL=0` 时必须仍然逐位等于 v25，否则 `§3.4.3` 那张表的前提就没了。
3. 最后把 `fir_pl_regs`（`src/rtl/fir_pl_regs.v`，36 项单元测试全过）接进 BD：
   第 5 个 MI 口，`aclk`/`resetn` 接 `FCLK_CLK0`/`peripheral_aresetn`，
   `gain_b0[15:0]…[127:112]` 取四段，其余输入先接 0。地址段用
   `board/overlay/build_audio_pl.tcl` 里现成的 `seg_of_pin`。

验收三条一条都不能省：

- 默认值（`GAIN=256 THR=419456 RATIO=2936064 ATK=REL=0`）下**逐位不变**
- 增益 0 → +24 dB **拖滑杆不咔**
- 改 `ATK/REL` 后压缩特性曲线能复现报告里那张图

## 构建（注意：**只能有一个实例**）

```bash
bash build/run_pl_build.sh          # 自带 8 轮重试 + PID 锁
tail -f tools/board_debug/_tmp/pl_build.log
```

**不要再开第二个。** 两个循环会往同一个 `build/vivado/audio_pl` 做
`create_project -force`（删目录重建），表现为"随机读不到自己的 tcl 文件"。
详见 `skill/pitfalls/vivado_windows_build.md`。

成功判据（不要 grep `### DONE`，那会命中被回显的 tcl 本身）：

- `tools/board_debug/_tmp/pl_build.rc` 出现 `round N OK`
- `board/overlay/fir_pl.bit` 的 mtime 比本轮 mark 新
- 日志里有 `### RESULT` / `### WNS_ns =`

## 出货版不受影响

`fir.bit`（16.2 ms 版）和 `build_fir.tcl` **一个字都没动**，是随时可切的退路。
板上 `/home/xilinx/dma_exp/fir.bit` = `5aa079f2…`，用 `tools/board_debug/i2c_with_fir.py`
验过 codec 读数正常。
