# 构建与上板指南

从一台没装过本项目的机器开始，到板子耳机里出声。全程大约 1–2 小时，
其中三成时间在等 Vivado。

> **这份指南已在 2026-11-02 完整跑过一遍，但用的不是一台全新的机器。**
> 干净的是源码：用 `git worktree add --detach` 拉出 HEAD 的无本地构建缓存副本，
> `./rebuild.sh csim/ip/bit` 与 `build/run_pl_build.sh` 全部走通；Vivado / Vitis HLS
> 仍是这台机器上原来装好的 2020.2。干净副本出的 `fir_pl.bit` MD5 是 `534a4b60…`，
> 与交付的 `628b75b3…` 不同 —— 布局布线本身不保证逐位复现；资源与时序在同一档
> （LUT 20497 对 20509、FF/BRAM/DSP 相同、WNS +0.065 对 +0.090），那颗位流还上板
> 跑过四项验收，全 PASS。所以"源码能重建出可用位流"已经证明；工具链安装、PYNQ
> 镜像烧录、license 这三步没有重做，不算验证过。详见 `data/results/clean_build_accept_20261102.md`。

> **两条构建线**：§1–§5 是块模式线（`fir.bit` + `fir_live.py`）的完整流程，属于上一代；
> 当前演示用的是 §6 的 PL 直连线（`fir_pl.bit` + `fir_pl_live.py`）。两条线的产物不通用。

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

`rebuild.sh` 只管块模式那一颗。仓库里另有几颗实验位流，各自一个脚本，
直接 `vivado -mode batch -source <脚本>` 跑：

| 脚本 | 环境变量 | 产物 |
|---|---|---|
| `board/overlay/build_rtl.tcl` | — | `fir_rtl.bit`（块模式外壳） |
| `board/overlay/build_rtl_dma.tcl` | — | `fir_rtl_dma.bit`（AXI-Stream + DMA） |
| `board/overlay/build_rtl_dma_cap.tcl` | — | `fir_rtl_dma_cap.bit`（抓取位流） |
| `board/overlay/build_audio_dma.tcl` | `FIR_AUDIO_DMA_WITH_FIR=0/1`、`FIR_AUDIO_DMA_TX_AW`（默认 10） | `fir_dma.bit` / `fir_dma_core.bit` / `fir_dma_tx<AW>.bit` |
| `board/overlay/build_audio_pl.tcl` | `FIR_PL_TX_TARGET`（默认 128） | `fir_pl.bit` / `fir_pl_tx<T>.bit` |
| `board/overlay/build_audio_i2sdbg.tcl` | — | `i2sdbg.bit`（内置音源 + 片内自收的诊断位流） |

**这些都不在演示通路上**，本节的块模式产物只有 `fir.bit`。它们的功能、延迟数字和适用范围在
`data/results/dma_pl_direct_20261025.md` 和 `data/results/rtl_vs_hls.md` §9。

---

## 3. 上板（块模式线，历史）

> 这一节对应的是上一代块模式通路：`fir.bit`、`/home/xilinx/jupyter_notebooks/`、`fir_live.py`。
> **当前演示的上板流程在 §6**（`fir_pl.bit`、`/home/xilinx/dma_exp/`、`fir_pl_live.py`），
> 目录、服务和脚本名都不一样，别混着照抄。

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

板上没有 SFTP，用 base64 推（`board/probes/perf_probe.py` 里的 `push()`
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

1. `rebuild.sh csim` 最后会跑 `sim/model/ref_model_wdrc.py --mode wdrc`，
   打印 `PASS：与 csim 逐点相同`；
2. 上板跑 `sh run_live.sh 30`，结尾必须是 `[OK ] 960000 / 960000 个采样逐位相同`。

两条都过，才算这次构建可用。

---

## 6. PL 直连版（另一条构建线）

上面的 `rebuild.sh` 出的是**块模式**位流（PS 在环）。PL 直连是另一条线：
`board/overlay/build_audio_pl.tcl` 从空工程摆 block design，产物是
`board/overlay/fir_pl.bit` + `fir_pl.hwh`。**两条线的产物不通用**，
`fir.bit` 里没有 `fir_pl_regs`，`fir_pl.bit` 里没有块模式的 DMA 环。

### 6.1 构建

```bash
bash build/run_pl_build.sh              # 完整构建（含 BD + 综合 + 实现 + 位流 + hwh）
BD_ONLY=1 bash build/run_pl_build.sh    # 只验 BD 接线，过了就停，几十秒
```

脚本自带三样东西，别自己另起一个循环：

- **PID 锁**（`build/tmp/probes/pl_build.lock`）：只允许一个构建循环；
- **每轮重试**（8 轮，轮间 60 s）：`create_bd_design` 的随机 rsb 读失败是常态，
  靠重试吸收，**不要去诊断**；
- **每轮换一个全新工程目录**（`build/vivado/pl_<轮>_<时间戳>`）：在残留目录上重跑
  几乎必挂，`rm -rf` 在 Windows 上又可能因为句柄没释放而漏删。

### 6.2 判据：WNS 必须 ≥ 0

脚本最后会打印 `### WNS_ns = ...`。**这一行要人眼看。** 位流编出来不等于能用：

当前演示位流（`board/overlay/fir_pl.bit`，md5 `628b75b3d7a13015a69125fb8ac2d228`）：
**WNS +0.090 ns、TNS 0、0 个失败端点（49,518 个端点全过）、WHS +0.007 ns**。

| 版本 | WNS | 说明 |
|---|---|---|
| 当前（`fir_pl.bit`） | +0.090 ns | 0 失败端点，可用 |
| 批 1 | +0.253 ns | 能用 |
| 批 2 第一版 | −1.612 ns | 529 个失败端点，照样出 bit，但会偶发算错 |
| 批 2 定版 | +0.136 ns | 能用（余量薄） |

WNS 为负时先看 `board/overlay/fir_pl_timing.rpt` 里第一条 VIOLATED 的起止点，
再去 RTL 里给那一级加一拍流水。逐级资源与路径见 `board/overlay/fir_pl_util.rpt`、
`board/overlay/fir_pl_timing.rpt`。

### 6.3 上板验收（不需要耳朵的两条）

```bash
cd board/probes
MSYS_NO_PATHCONV=1 python board_ssh.py --put ../../board/overlay/fir_pl.bit /home/xilinx/dma_exp/fir_pl.bit
MSYS_NO_PATHCONV=1 python board_ssh.py --put ../../board/overlay/fir_pl.hwh /home/xilinx/dma_exp/fir_pl.hwh

# ① 寄存器窗口：ID、出厂值、四段写读、斜坡 busy
MSYS_NO_PATHCONV=1 python board_ssh.py --sudo \
  "cd /home/xilinx/dma_exp && XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u pl_regs_check.py"

# ② 压缩器动不动：话筒当源（安静房间里量不出东西，会报 PASS(partial)）
... pl_drc_probe.py
# ②' 或者用播放环里的 440 Hz 正弦当源（要放音，白天再跑）
... pl_drc_ply_probe.py
```

`pl_drc_probe.py` 会先量当前包络再定门限。**如果它报 `PASS(partial)`，
不是 RTL 坏了**，是话筒没信号（午夜环境噪声只有 −100 dBFS），换 ②' 或白天重跑。

### 6.4 网页第三档

两个进程，必须这个顺序（overlay 只能被一个进程持有）：

```bash
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_live.py &
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_webui.py --port 8080
```

浏览器开 `http://192.168.2.99:8080/`。接口与文件协议写在 `board/scripts/fir_pl_webui.py`
和 `fir_pl_live.py` 的文件头里。

### 6.5 坑（2026-10-28 踩的）

**两个构建循环同时在跑，会把对方杀在半路。** 表现是每一轮都报不同的
"couldn't read file ..."，看着像 rsb 的随机失败，其实是并发。
起因是"停掉后台任务"只杀了外层 shell，`bash build/run_pl_build.sh` 还在跑：

```bash
ps -ef | grep "bash build/run_pl_build.sh" | grep -v grep   # 应该只有 1 个
MSYS_NO_PATHCONV=1 tasklist /FI "IMAGENAME eq vivado.exe"   # 应该没有
```

**`taskkill` 之后要验尸**，别直接进下一轮（脚本里已经带了这个检查）。

**复用已有工程能省一半时间**：BD 没变、只改了 RTL 时，用
`build/reuse_batch2.tcl`（把里面的工程路径换成最近一次成功的目录）直跑
综合→实现，绕开 `create_bd_design`。
