# PYNQ-Z2 板子出声 / 录音

出厂 base overlay 的音频通路怎么跑通，以及四个会卡住人的地方。
实测：PYNQ-Z2 + PYNQ 2.7.0 + 出厂 base overlay，2026-09-27。

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

## 板子上只有两个 3.5mm 口，而且外放和拾音互斥

| 口 | 方向 | 接什么 |
|---|---|---|
| `HP + Mic` | 输出 + 麦克风输入，**一口两用**（TRRS 四段） | 耳机 / 有源音箱 |
| `Line-in` | 只能输入 | 电脑耳机口（当测试音源） |

有源音箱必须插 `HP + Mic`（`Line-in` 是输入口，插不了）。**插上音箱就没有麦克风了。**

演示前先定走哪一种，两种的数字通路是同一条，代码不用改（区别只是 codec 选哪个输入口）：

- 说话型：带麦耳机，麦和听都走 `HP + Mic` —— 外放时容易啸叫
- 放音频型：电脑 → 公对公线 → `Line-in`，板子 → 音箱 —— 不啸叫，前后对比更清楚

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
