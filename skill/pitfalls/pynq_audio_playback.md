---
name: pynq-audio-playback
description: >
  Get sound out of and audio into a PYNQ-Z2 through the factory base overlay,
  and past the seven things that block it: root-only device nodes, a wrong volume
  ceiling, the 24-bit stereo 48 kHz wav restriction, DC offset on capture, the
  headset switch that latches once on insertion (and may need many replugs), a
  codec that stays unconfigured until some script configures it, and capture
  reading near full scale — which is a sign-extension bug in your own read path,
  not a hardware fault.
  Use when the board is silent, when the microphone records only
  noise floor, when set_volume raises ValueError, when Audio.load rejects a wav
  file, when capture reads near full scale, or when planning how to demonstrate
  audio I/O.
license: MIT
compatibility: PYNQ-Z2 image 2.7.0 + factory base overlay
metadata:
  version: "1.1.0"
  updated: "2026-10-15"
---

# PYNQ-Z2 板子出声 / 录音

出厂 base overlay 的音频通路怎么跑通，以及七个会卡住人的地方。
实测：PYNQ-Z2 + PYNQ 2.7.0 + 出厂 base overlay，2026-09-27。

## 什么时候用

- 板子不出声，或者录音全是本底噪声
- set_volume 报 ValueError: Volume has to be in [0,63]!
- Audio.load() 拒绝一个 wav 文件
- 自己编的库读回来的电平贴着满量程，而且你不说话它也不掉（**先看坑 7：这是读法，不是硬件**）
- 要确认音频搬运到底是 CPU 干的还是 FPGA 干的
- 演示前要定用哪种接法（耳麦还是 Line-in）

## 什么时候别用

- 要把自定义核接进音频通路 —— 这一页讲的是出厂 base overlay 的通路
- 只需要验算法、不用真出声 —— Python 基线和 C 仿真就够

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-27 | 初版 |
| 1.0.1 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |
| 1.0.2 | 2026-10-10 | 坑 5 补实测：拔插**一次不一定够**，要拔到电平表跳起来为止（10/09 连续拔插到第 35.5 秒才跳） |
| 1.0.3 | 2026-10-10 | 新增坑 7：边录边放时麦克风输入会被推成满幅自激（定位到 `stream_begin` 的 4 次 I2C 写，但留了一处待复现的矛盾） |
| 1.0.4 | 2026-10-13 | 坑 7 定案：环路的另一头是**耳机功放**、环路增益只是刚刚超过 1（更正"不是声学反馈"那条证据）；修法 = 话筒 boost 降 20 dB，并写明加完要重编板上 `.so` |
| 1.0.5 | 2026-10-13 | 坑 7 修法改口径：**只降 boost 不够**（同一状态 probe6 静、probe7 满一遍），要两级一起调 = `R5/R7=0x08` + `R8/R9=0xD3`（+41 -> +27 dB），并补上重编 `.so` 后符号表里能看到 `stream_reg_write` 的实测 |
| 1.0.6 | 2026-10-13 | 坑 7 再加一条**反证**：这套修法装进 `fir_live.py`、库重编后，**实时跑 20 秒还是贴顶**（18330 / 31671）。原因是这类探针有两个结构盲点 —— 每步只 0.2 秒（比环路的增长时间常数短）、TX 全填 0（看不见"要有输出才成立"的那条） |
| 1.0.7 | 2026-10-13 | 坑 7 **定案（第三次，也是对的这次）**：把窗口从 0.2 秒拉到 3 秒后，"降话筒增益"这个修法整个作废 —— 同一寄存器状态头三格 286/74/20、第五格就 29070。并且**话筒输入整级 mute 照样满**、**输出拧到 −57 dB 照样满** —— 环路不经过话筒链路、也不靠输出幅度。剩 `R29/R30=0x02` 一档待用 3 秒重测。教训写进正文：**观测窗口是实验设计的一部分** |
| 1.0.8 | 2026-10-13 | 坑 7 **把最后一档也测了**：`R29/R30=0x02`（耳机功放整个关掉，回读确认真关上了）在 3 秒里照样爬满，与开着功放无异；"功放关 + 话筒整级 mute"也是第三格起爬。**probe6 那张表整张作废（全部是 0.2 秒窗口假象），环路两端都不在 codec 模拟侧** → 下一步查数字侧（RX 读回的不是 ADC 输出）或 ALC 自动增益 |
| 1.0.9 | 2026-10-13 | 坑 7 **把地基也推翻了**：一次 I2C 写都不做、只 `capture_block`（连 `stream_begin` 都不调），3 秒里照样爬满（三条件 × 两遍，六行全爬）。**`stream_begin` 那 4 次 I2C 写是清白的，`cap_probe2` 的"只收不放就安静"也是窗口假象** → 这条线上没有一个"写某个值就能治好"的位置；顺带：3 秒里 R11–R16 读回来一字未变（软件没在改寄存器），以及"每个 chunk 第一个块读出安静"这个未解释的读通路线索 |
| 1.0.10 | 2026-10-14 | 坑 7 **结案：那个"自激"从头到尾不存在。** 24 位补码没做符号扩展（bits[31:24] 被当 0），把"数字零的抖动"读成 **rms 46341**。10-10 到 10-13 那三晚的"模拟环路 / 双稳态"推理全部作废 —— 不是硬件。那条"每个 chunk 第一个块读出安静"的线索也一并消失（是缓冲区对齐，见正文） |
| 1.1.0 | 2026-10-15 | 坑 7 正文整篇重写：留**结论 + 判据 + 四条可迁移的教训**，删掉作废的推理链（原文去 `git show 9cbc330:skill/pitfalls/pynq_audio_playback.md`）。坑 5 补 10/15 实测（拔插到第 **18** 秒跳起来；前 17 秒 rms 13~17 死水）。补坑 7 的真凶之一：`fir_live.py` 那四行 `stream_reg_write` 确实白掉 ~11 dB 增益 |

## 跑通它

从串口控制台（`xilinx` 用户）：

```
cd /home/xilinx/jupyter_notebooks && echo xilinx | sudo -S env XILINX_XRT=/usr \
    /usr/local/share/pynq-venv/bin/python3 verify_audio_playback.py
```

不带界面的脚本：`board/scripts/verify_audio_playback.py`
带波形图的 notebook：`board/notebooks/w2_audio_playback.ipynb`

核心就四行：

```python
audio = BaseOverlay("base.bit").audio   # 开机时出厂固件已经灌好了
audio.set_volume(62)
audio.load("x.wav")                     # 24 bit / 双声道 / 48 kHz
audio.play()
```

## 坑 1：命令行报 `OSError: Root permissions required.`

同一份代码在 Jupyter 里能跑，在串口控制台报这个错。差别不在代码，在「谁在跑」。

PYNQ 靠 `/dev/mem` 和 `/dev/uio*` 访问 FPGA 寄存器，两者都只有 root 能开：

```
$ ls -l /dev/mem        ->  crw-r----- root kmem
$ groups xilinx         ->  xilinx adm sudo        (不在 kmem 组)
$ ps -eo user,pid,comm | grep jupyter  ->  root jupyter-noteboo
```

Jupyter 服务本身是 root 起的，串口登进来的 `xilinx` 不是。

加 sudo，并且显式给 `XILINX_XRT` —— `sudo` 不读 `/etc/profile.d/` 里的开机设置：

```
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 脚本.py
```

## 坑 2：`set_volume(63)` 报 `ValueError: Volume has to be in [0,63]!`

报错信息里的区间是错的。源码判断是 `if not 0 <= volume < 63`。

**上限实际是 62。**

## 坑 3：`load()` 只吃一种 wav

`Audio.load()` 按每个采样 3 字节解包，只接受 **24 bit / 双声道 / 48 kHz**。
采样率固定 48000（codec 决定的，改不了）。

素材太安静时，不要靠猜着加音量。直接生成一段**数学上已知干净**的正弦波来判断：
如果放它还听到杂音，问题就在板子这一侧；如果是干净的一声蜂鸣，那杂音来自素材。
现成的生成函数在 `board/scripts/verify_audio_playback.py` 的 `make_tone()`。

## 坑 4：录回来的信号带直流偏置

录一段安静的信号，波形不围绕 0 摆动，整体抬高约满量程的 1.2%，还缓慢下滑。
这是采集通路的直流偏置，不是 bug。

算频谱或做前后对比之前先减均值：

```python
x = x - x.mean()
```

不减的话，频谱在 0 Hz 处会出现一根很高的假峰，把真正要看的东西压下去。

## 坑 5：耳机麦插着也不出声，要拔插到电平表跳起来为止

`HP + Mic` 口里有一颗 TI **TS3A226AE** 自动耳机开关（原理图 U41，9 球封装），
负责判断插头是 CTIA 还是 OMTP，再把麦克风接到正确的那根触点上。
**CTIA/OMTP 这一层不用管**——板子两种都认，这颗片子就是专门干这个的。

它的检测由插头插入时 `EN` 脚上的一个上升沿触发，**检测一次就记住结果**。
板子上电时插头已经插在里面，这一下就不存在了，开关停在「麦克风没接上」的状态。

症状：录音有效值只剩本底，左右两路都不动，说话、敲麦都没反应。

做法：板子开着，把耳机**拔掉再插回去**，然后重跑录音脚本看电平表。
**一次不一定够 —— 要拔插到电平表跳起来为止。**

10/09 实测：连续拔插，到**第 35.5 秒**电平才跳起来，之后一直是真的。
10/15 复测（`board/scripts/cap_probe16.py`，连采 90 秒、每秒一行、人一边拔插一边"嗯————"）：
**前 17 秒 rms 13～17 是一片死水，第 18 秒峰蹦到 45104**，之后一直活着
（rms 几百～2842、峰上千）；**中途掉回 rms 12 的那几秒就是插头拔出来的空档**。
所以「拔了一次还是没声」不等于这条路不通，是还没插到那一下。

**别在增益上打转。** 这个故障期间话筒 PGA 是好的、寄存器也写进去了：
把 boost 从 +20 dB 降到 0 dB、输入音量从 +26 dB 降到 +2 dB，读数**几乎不动**
（那 11 dB 的差就是这两级增益本身）。**"说话/敲麦都没反应"、两条声道一样地趴在
本底上，就直接去拔插。**

拔插前后实测（`board/scripts/mic_watch.py`，int16 域有效值）：

| 状态 | 有效值 | 峰值 |
|---|---|---|
| 插着但没检测到 | 7 ～ 45 | 70 ～ 113 |
| 拔插一次之后说话 | 400 ～ 1200 | 2000 ～ 5400 |
| 拔插到第 35.5 秒跳起来（10/09） | 11822 | 32768 |
| 拔插到第 18 秒跳起来（10/15） | 2842 | 45104 |

麦克风是单声道，被同时送进左右两个声道，所以两边数字几乎一样。

**实测的判别线**（拿 `cap_probe16.py` 那张每秒的表看）：**说话时 rms 几百～2400、
峰上千；插头没锁对时 rms 13～17、峰 50～422。** 差两个数量级，不用猜。

**运维后果（演示前必做）：** 插头一动、或者一断电，都可能又锁错。
**演示前先拔插到电平跳起来（实测要 18 秒），再开始，全程别碰插头。**

依据：`TUL_PYNQ_Schematic_R12.pdf` 第 7 页 "USB & Audio"。插口是 `CN_AUDIO1`
（SJ-43515TS-SMT），四段触点走 `FRONT_L` / `FRONT_R` / `RING2` / `SLEEVE`，
后两根都接到 U41；U41 的 `EN` 脚经 1 MΩ（R681）上拉到 AVCC3V3，
并接插口自己的插入检测触点，正是 TI 数据手册推荐的那种接法。

## 板子上只有两个 3.5mm 口，而且外放和拾音互斥

| 口 | 方向 | 接什么 |
|---|---|---|
| `HP + Mic` | 输出 + 麦克风输入，**一口两用**（TRRS 四段） | 耳机 / 有源音箱 |
| `Line-in` | 只能输入 | 电脑耳机口（当测试音源） |

有源音箱必须插 `HP + Mic`（`Line-in` 是输入口，插不了）。**插上音箱就没有麦克风了。**
耳机麦插着却录不到声音，先看坑 5——多半要拔了重插，而且要拔插到电平表跳起来为止。

演示前先定走哪一种，两种的数字通路是同一条，代码不用改（区别只是 codec 选哪个输入口）：

- 说话型：带麦耳机，麦和听都走 `HP + Mic` —— 外放时容易啸叫
- 放音频型：电脑 → 公对公线 → `Line-in`，板子 → 音箱 —— 不啸叫，前后对比更清楚

## 坑 6：上电后耳机里一直嗡嗡响 —— codec 还没被配置

板子一上电，PYNQ 会自动加载出厂的 base overlay（`fpga_manager/fpga0/state` 会是
`operating`），但**不会**替你做 `codec.configure()`。这一步没走之前，耳机口停在
未配置的状态，听起来是一大团嗡嗡声。

症状：刚开机、还没跑过任何脚本，插上耳机就有很大的底噪。

做法：跑一次任何调了 `codec.configure()` 的脚本就行 ——
`board/scripts/replay_own_overlay_tone.py` 就是干这个的（装 overlay → configure →
放三遍干净正弦）。跑完嗡嗡声立刻消失。

最快的一条判据，是看板子**开机之后到底有没有跑过脚本**：

```sh
uptime                                                    # 开机多久了
find /home/xilinx/jupyter_notebooks -newermt "<开机时刻>"   # 开机后有没有文件被改过
```

一条都没跑过，那这声音就不是程序发出来的，先怀疑这一条，别先去翻代码。

实测（10/08）：开机后未配置 → 嗡嗡声；跑一次 `configure()` 之后 → 三遍
1 kHz 干净正弦每遍都是干净的一声「嘟」，停下之后也没有底噪。
**实时通路代码一个字没改** —— 它本来就不是坏的。

## 坑 7：录回来的电平贴着满量程 —— 先怀疑自己的读法

**症状**：自己编的 C 库（`capture_block` / `duplex_block`）读回来的电平贴着满量程
（16 位域 rms 三万多），**你不说话它也不掉**，耳机里是一声很响的噪声；
而在同一个麦克风口、同一时刻，PYNQ 自己的 `audio.record()` 读出来是安静的（rms 28）。

**原因：寄存器里低 24 位是二进制补码，读的时候忘了做符号扩展。**

I2S 寄存器有效的是低 24 位，`bits[31:24] = 0`。直接当无符号数读，
**任何负采样都会变成一个接近满量程的大正数**。而这条采集通路本身带直流偏置
（坑 4）：干净通路上原始均值约 1.0e5，换算到 16 位域约 **31679** ——
也就是 24 位域的 **2^23 × 0.966，正好压在符号翻转点上**。于是"数字零"周围那点抖动，
一部分采样落在大正数那一侧，**rms 就等于 65535·√p**（p = 落在那一侧的样本占比），
和真实信号幅度完全无关。

十六进制 dump 是实锤：

    `00FFFF9B` = -101        `00FFFFF8` = -8      （符号扩展之后才对）
    当无符号读  = 65535 量级                        （46341 就是这么来的）

**做法**：截低 24 位、判符号、再除 256。

```python
c = raw.astype(np.int64) & 0xFFFFFF
s = np.where(c >= (1 << 23), c - (1 << 24), c).astype(np.float64) / 256.0
```

`0xFFFFFF` 截出 24 位，`>= 2^23` 说明是负数、减 `2^24` 补回来，最后 `/256` 落到
16 位域（满量程 32767）。两个声道一起截：`c.reshape(-1, 2)`。

**判据：满量程读数如果恰好等于一个漂亮的理论值，先怀疑读法，不要怀疑硬件。**
这里那条是 **46341 = 65535/√2**。硬件故障不挑这么整的数。

顺带解释掉两件当年没看懂的事：

- 那个"停几格 → 单调爬 → 停在 40000~53000"的形态，是 **p 从 0 涨到 0.5**
  （直流偏置缓慢下滑，越来越多采样掉到偏置的另一侧），不是自动增益慢慢推上来。
- "每个 chunk 的第一个块读出来是安静的"，也是读法 / 缓冲区对齐，不是通路现象。

### 四条可迁移的教训（这一段比结论值钱）

1. **满量程读数先过一遍读法。** 遇到"贴着满量程"先问一句：我截位了吗？符号位在哪？
   有没有哪个数看着像理论值（46341、32767、65535、1.0）？
2. **观测窗口是实验设计的一部分。** 这一路上所有"某个寄存器状态下是安静的"结论，
   都是 0.2 秒窗口量出来的 —— 假象。**只要被测对象可能慢慢长起来，窗口就得比它的
   时间常数长几倍**，而且要**逐格打电平**（信息全在"第几格开始跳"里面）。
3. **寄存器假设必须回读 + 长窗口才算数。** `stream_reg_write` 的返回值别丢，
   写完回读一眼；同一状态至少跑 3 秒，并且**换个顺序再跑一遍**（排除顺序量）。
4. **同一进程内连着做三段、每段只差一个动作**（不写 / 只写 I2C / 写 TX），
   比"改一次跑一次想一次"快得多，结论也硬。上面那些表就是这么量出来的。

### 这次排查的意外收获：真凶不在"自激"这条线上

`fir_live.py` 里那四行 `stream_reg_write`（`0x0B/0x0D=0x08`、`0x0E/0x0F=0xD3`）
是当年当"止住自激的修法"加进去的 —— 而自激是假的，所以它们**纯粹是白掉增益**：
`cap_probe14/15` 用 A1/C1/A2/C2 交叉跑（同一个人从头到尾说话）证明读数
**跟着寄存器来回跳**（rms 11.1 → 3.4 → 12.6 → 2.3），比 PYNQ 原样的两级增益低约
**11 dB**。2026-10-15 已删除。

话筒真的没信号，是另一条线 —— **看坑 5（U41 自动耳机开关要拔插到电平跳起来）**。

**顺带：** `stream_reg_write` 是后来加进 `audio_stream.cpp` 的，**加完必须重编板上那份库**，
否则调用它的脚本报
`AttributeError: function/symbol 'stream_reg_write' not found in library`：

    sh /home/xilinx/jupyter_notebooks/build_audio_stream.sh

（脚本末尾会 `nm -D --defined-only` 把导出符号打出来，对着看一眼最省事。10/13
实测：重编之后符号表里出现 `00001528 T stream_reg_write`，调用它的脚本才跑得起来。）

## 实测：搬运是 CPU 干的，不是 FPGA

放一段 3 秒音频：

| 动作 | 墙钟 | CPU |
|---|---|---|
| 播放 | 2.96 s | 2.96 s |
| 录音 | 2.95 s | 2.95 s |

芯片是双核，所以「CPU ≈ 墙钟」= **一个核被 100% 占满**。

旁证：`audio_codec_ctrl_0` 在 `.hwh` 里只有一条 BUSINTERFACE —— `S_AXI`（从接口），
音频通路里没有任何 DMA。它自己没有去内存取数据的能力，数据只能由 CPU 写进去。

量 CPU 时间要注意 `resource.getrusage` 返回的是**进程累计值**，直接读会把加载 overlay
的时间一起算进来，得出过「墙钟 2.96 s / CPU 13.21 s」这种超过双核上限的数。必须前后差分。
