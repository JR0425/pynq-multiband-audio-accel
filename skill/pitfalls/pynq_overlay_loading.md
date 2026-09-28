# PYNQ-Z2 加载 Overlay 的前置条件

`Overlay("xxx.bit")` 报 `No Devices Found`，加 `sudo` 也一样。
实测：PYNQ-Z2 + PYNQ 2.7.0，2026-09-26。

## 两个原因

**1. 用错了解释器**

板子上有两个 python：`/usr/bin/python3` 没有 pynq；pynq 装在 venv 里——
`/usr/local/share/pynq-venv/bin/python3`。

`/home/xilinx/pynq` 只是指向该 venv 的软链，不是装好的库。

**2. 缺环境变量 `XILINX_XRT`**

PYNQ 在导入设备驱动前会检查这个变量，没有就直接跳过，于是报「找不到设备」：

```
# pynq/pl_server/__init__.py
if 'XILINX_XRT' in os.environ:
    from .xrt_device import XrtDevice
```

变量由 `/etc/profile.d/xrt_setup.sh` 设置，而它**只在登录 shell 里执行**。
SSH 直连甩一条命令属于非登录 shell，拿不到这个变量。

## 正确的调用

```
env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 your_script.py
```

## 验证加载成功

```
from pynq import Overlay
ol = Overlay("fir_accel2.bit")
print(ol.ip_dict)     # 应打出 IP 名和地址，如 filter/fir_dma → 0x40400000
```

## 验硬件算得对不对：用互相关，不要逐点比

FIR 有固有延迟，逐点比对会得到一个很低的相关系数，看起来像算错了。
正确做法是扫一遍位移，取相关系数最高的那个：

```
best = max(range(-30, 31),
           key=lambda s: np.corrcoef(hw[3000+s:n-3000+s], ref[3000:n-3000])[0, 1])
```

实测最佳位移 **+13**，正好等于 27 抽头 FIR 的群延迟 `(N−1)/2`，
该位移下相关系数 **1.000000** —— 硬件与软件完全一致，只差一个固定的 13 点延迟。

## 实测数据

- 20 万点 int32 输入，DMA 传输约 **3.1 ms**
- 同一任务：软件 0.0922 s / 硬件 0.00469 s → **19.67 倍**

## 自己出 bit 时另外两个坑

实测：Vivado 2020.2 batch 模式，建一个只放 Zynq PS 的最小 overlay，2026-09-27。

**1. `write_hwdef` 出的不是 `.hwh` 文件，是一个 zip 包**

```
write_hwdef -force -file ps_only.hwdef
```

出来的 `ps_only.hwdef` 用 `file` 一看是 `Zip archive data`。包里装的是：

```
    1210  hwdef.xml          <- 索引清单，不是 hwh
  119162  ps_only.hwh        <- 这个才是
  499373  ps7_init.c
2761441  ps7_init.html
   33923  ps7_init.tcl
   ...
```

PYNQ 是拿 `ElementTree.parse()` 直接把这个文件当 XML 读的：

```
# pynq/pl_server/hwh_parser.py 第 159 行
tree = ElementTree.parse(hwh_name)
```

塞一个 zip 进去，`Overlay()` 当场挂。**要的是包里那个根标签 `<EDKSYSTEM>`、
一百多 KB 的 `<名字>.hwh`**，拆出来单独存：

```python
import zipfile
zipfile.ZipFile("ps_only.hwdef").extract("ps_only.hwh", ".")
```

判据（不用跑板子就能验）：文件头应该是 `<?xml`，根标签 `<EDKSYSTEM>`，
里面 `<MODULE INSTANCE="ps7_0" ...>`。若根标签是 `<Project>`、只有 1 KB，那是 `hwdef.xml`，拿错了。

**2. PS 使能的每个 AXI 端口，时钟脚都要接**

只给 `M_AXI_GP0_ACLK` 和 `S_AXI_GP0_ACLK` 接上时钟不够。PS 配置里
`M_AXI_GP1` / `S_AXI_HP0` / `S_AXI_HP2` 只要是开的，它们的 ACLK 也必须接，
否则 `validate_bd_design` 直接失败：

```
ERROR: [BD 41-758] The following clock pins are not connected to a valid clock source:
/ps7_0/M_AXI_GP1_ACLK
/ps7_0/S_AXI_HP0_ACLK
/ps7_0/S_AXI_HP2_ACLK
```

官方 base overlay 的接法是（`base.tcl` 第 4640 / 4642 行）：

```tcl
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK1] [get_bd_pins ps7_0/S_AXI_HP0_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK3] \
               [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP2_ACLK]
```

**为什么值得先建一个空的 PS 工程**：这个错停在 `validate_bd_design`，
还没进综合，几十秒就报出来；要是等整套音频 IP 都画完再跑，同样的错要几分钟才知道。

## 相关

- 串口进板子：`skill/pitfalls/pynq_serial_console.md`
- 网络配置：`skill/pitfalls/pynq_direct_ethernet_windows.md`
- 整条音频通路是怎么接的（源码级）：`skill/pitfalls/pynq_audio_playback.md`
