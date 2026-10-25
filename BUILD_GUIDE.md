# 构建与上板指南

从一台没装过本项目的机器开始，到板子耳机里出声。全程大约 1–2 小时，
其中三成时间在等 Vivado。

> **这份指南还没在一台真正的干净机器上跑过。**

---

## 1. 需要什么

| 项 | 版本 | 说明 |
|---|---|---|
| Vivado | 2020.2 | 出整机位流 |
| Vitis HLS | 2020.2 | C 仿真、综合、导出 IP |
| 器件 | `xc7z020clg400-1` | PYNQ-Z2 |
| Python | 3.9+（主机侧） | 跑参考模型和比对脚本 |
| PYNQ 镜像 | 2.7.0 | 板上 |
| 硬盘 | 工具装完约 60 GB | Vivado + Vitis HLS 都不小 |

两个工具建议装在**同一个 Xilinx 根目录**下（当初这台机器是 `E:\Xilinx`）。
装到别处不用重装，改环境变量就行，见 §2。

**License**：Vivado/Vitis HLS 2020.2 需要 WebPACK 或以上。装完打开一次
Vivado，能在 Help → About 里看到 license 状态就行；本项目没用到需要商业授权的 IP。

---

## 2. 一条命令构建

```bash
git clone <仓库地址> pynq-multiband-audio-accel
cd pynq-multiband-audio-accel
python -m pip install -r requirements-python.txt

# 工具不在默认位置时先指过去（Git Bash 路径写法即可）
export VIVADO_BIN="D:/Xilinx/Vivado/2020.2/bin/vivado.bat"
export VITIS_HLS_BIN="D:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat"

./rebuild.sh
```

`rebuild.sh` 分三步，可以从中途开始：

| 命令 | 做什么 |
|---|---|
| `./rebuild.sh csim` | 只跑 HLS C 仿真 + 和整数参考模型逐点比对（改算法后先跑这个） |
| `./rebuild.sh ip` | 导出 IP，然后出整机位流（改了核就必须走这档） |
| `./rebuild.sh bit` | 只重出位流（只改了 block design / 约束时用） |
| `./rebuild.sh` | 三步全跑 |

**改过 `src/hls/*.cpp` 就必须至少跑到 `ip`。** 只跑 `bit` 用的是仓库里上次的
IP 包，C 代码的改动一点都进不去。

### 三步各多久（这台机器实测）

| 步 | 时间 | 备注 |
|---|---|---|
| C 仿真 | 1–2 分钟 | 大部分时间在建 HLS 工程 |
| 导出 IP | 3 分钟 | 综合 173 s + 打包 |
| 整机位流 | 5–10 分钟 | 综合 1–2 分、布线 35 s、出 bit 15–30 s |

第一次构建还要多下音频 IP 的源码（`fetch_audio_ip.py`），看网速。

### 产物

| 文件 | 用途 |
|---|---|
| `board/overlay/fir.bit` | PYNQ 加载的位流 |
| `board/overlay/fir.hwh` | 和位流配套的硬件描述，缺了不能加载 |
| `build/vivado/ip_repo/fir_multiband_v1_0/component.xml` | 核的 IP 包 |

位流和 hwh **必须成对**拷到板上，版本对不上 PYNQ 会拒绝加载。

### 其它 overlay 的构建脚本

`rebuild.sh` 只管出货那一颗。仓库里另有几颗实验位流，各自一个脚本，
直接 `vivado -mode batch -source <脚本>` 跑：

| 脚本 | 环境变量 | 产物 |
|---|---|---|
| `board/overlay/build_rtl.tcl` | — | `fir_rtl.bit`（块模式外壳） |
| `board/overlay/build_rtl_dma.tcl` | — | `fir_rtl_dma.bit`（AXI-Stream + DMA） |
| `board/overlay/build_rtl_dma_cap.tcl` | — | `fir_rtl_dma_cap.bit`（抓取位流） |
| `board/overlay/build_audio_dma.tcl` | `FIR_AUDIO_DMA_WITH_FIR=0/1`、`FIR_AUDIO_DMA_TX_AW`（默认 10） | `fir_dma.bit` / `fir_dma_core.bit` / `fir_dma_tx<AW>.bit` |
| `board/overlay/build_audio_pl.tcl` | `FIR_PL_TX_TARGET`（默认 128） | `fir_pl.bit` / `fir_pl_tx<T>.bit` |
| `board/overlay/build_audio_i2sdbg.tcl` | — | `i2sdbg.bit`（内置音源 + 片内自收的诊断位流） |

**这些都不在演示通路上**，出货演示只有 `fir.bit`。它们的功能、延迟数字和适用范围在
`data/results/dma_pl_direct_20261025.md` 和 `data/results/rtl_vs_hls.md` §9。

---

## 3. 上板

### 3.1 网络（直连，没有路由器）

- 板子 eth0 固定 `192.168.2.99/24`
- 笔记本那张网卡固定 `192.168.2.1/24`，网关和 DNS 留空
- Jupyter：`http://192.168.2.99:9090`，账号密码都是 `xilinx`
- 网页调参界面：`http://192.168.2.99:8080/`

**网页界面靠系统服务开机自启**（见 §3.2）。没装那个服务时 8080 是拒绝连接的，
而 ping 和 9090 都正常 —— 那不是板子坏了，只是服务没起：

```bash
cd /home/xilinx/jupyter_notebooks
setsid nohup sh run_webui.sh > /dev/shm/webui.log 2>&1 < /dev/null &
```

**别写反了。** `192.168.2.1` 是笔记本自己的地址，那台机器上 8080 端口
被另一个服务占用，浏览器会返回 `Access Error: 404`，看起来像板子没起来。

### 3.2 装一次就行的东西

**死机修复是个系统服务，不是拷文件。** 镜像重刷过就要重装：

```bash
sudo sh /home/xilinx/jupyter_notebooks/install_ksoftirqd_rt.sh
systemctl is-active ksoftirqd-rt.service          # 应为 active
chrt -p $(pgrep -f '^ksoftirqd/0$')               # 应为 SCHED_FIFO 50
```

没有它，实时通路跑一分钟左右会 panic。

**网页界面也做成服务了，装一次之后开机自己起来：**

```bash
cd /home/xilinx/jupyter_notebooks
echo xilinx | sudo -S install -m 644 fir-webui.service /etc/systemd/system/
echo xilinx | sudo -S systemctl daemon-reload
echo xilinx | sudo -S systemctl enable --now fir-webui.service
systemctl is-enabled fir-webui.service            # 应为 enabled
```

服务文件在仓库的 `board/scripts/fir-webui.service`，要和 `fir_webui.py` 一起拷到板上。
没装服务时 `sh run_webui.sh` 手动起也行，但每次重启都要重来一次。

### 3.3 拷文件

板上所有东西都在 `/home/xilinx/jupyter_notebooks/`。要拷的是：

- `fir.bit`、`fir.hwh`
- `fir_live.py`、`fir_core.py`、`fir_webui.py`、`run_live.sh`、`run_webui.sh`
- `page.html`（网页界面，放不放都行；放了就用它当首页）
- `libaudio_stream.so`（**改过 `board/scripts/audio_stream.cpp` 才需要重编**）

板上没有 SFTP，用 base64 推（`tools/board_debug/perf_probe.py` 里的 `push()`
就是这个做法，并且会校验 md5 —— **只信返回码不够，曾经返回 0 而文件没换**）。

### 3.4 跑起来

```bash
cd /home/xilinx/jupyter_notebooks
sh run_live.sh 30        # 麦克风 → 核 → 耳机，跑 30 秒
sh run_live.sh 0         # 一直跑，Ctrl-C 停
```

**先插拔耳机到电平跳起来再开始**（板载耳机座上的 U41 只在插入的上升沿检测一次，
实测要插拔到第 18 秒左右），之后全程别碰插头。

---

## 4. 常见坑

**1. 导出 IP 时 `ERROR: [IMPL 213-28] Failed to generate IP`，日志里有
`bad lexical cast`。** 这是 Vivado 的 Y2K22 bug：打包时把日期写进 `core_revision`，
2022 年之后是 10 位数，超过 32 位有符号上限。`build/hls/run_export_ip.tcl`
里已经处理了（承接失败 → 改小那个数 → 重打包），用 `rebuild.sh` 不会碰到。

**2. `Could not create slave interpreter '::ipgen_iptclns'`。** IP 生成线程建
Tcl 子解释器失败，和设计无关。删掉旧的 Vivado 工程目录重跑就过：

```bash
rm -rf build/vivado/fir
```

**3. 8080 打不开。** 见 §3.1：多半是把 `192.168.2.1` 当成板子了。
换端口前先确认新端口上没有本机其他服务占用。

**4. `Vivado` 说找不到器件 `xc7z020clg400-1`。** 装的时候没勾 Zynq-7000 器件族。

**5. 板上加载位流失败。** `fir.bit` 和 `fir.hwh` 不是同一次构建的产物。
两个一起重新拷。

**6. 实时跑起来耳机里断续。** 先看有没有装 §3.2 那个服务，再看是不是改过
`audio_stream.cpp` 却没重编 `.so`。

**7. `create_bd_design` 随机报 `couldn't read file ...: no such file or directory`，
或者 `Failed to load feature 'ipintegrator'`，每次点到的文件还不一样。**
这是 Vivado 2020.2 在 Windows 上的偶发问题，和设计无关 —— 同样的脚本前一次能过、
这一次过不去，过一阵子再跑又会过。**处理办法就是删干净工程目录重试**：

```bash
rm -rf build/vivado/<工程名>
```

一遍过不去就换个时间再跑几遍。2026-10-25 编 `fir_pl.bit` 时连续挂了 8 次，
第 9 次自己好了；`i2sdbg.bit` 和抓取位流也各碰到过一次。**重试之前一定先
`rm -rf`**，在残留目录上重跑几乎不会成功。

**8. 某个 IP 的综合结果里 DSP 数是 0。** 那是空壳位流（曾经出过一次）。
`build_rtl*.tcl` 在综合后检查 DSP 数，少于 100 直接报错，用它们构建不会中招。

---

## 5. 怎么知道构建是对的

构建完不只看"没有报错"，还要过两道：

1. `rebuild.sh csim` 最后会跑 `tools/ref_model_wdrc.py --mode wdrc`，
   打印 `PASS：与 csim 逐点相同`；
2. 上板跑 `sh run_live.sh 30`，结尾必须是 `[OK ] 960000 / 960000 个采样逐位相同`。

两条都过，才算这次构建可用。
