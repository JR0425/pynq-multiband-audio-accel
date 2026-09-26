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

## 相关

- 串口进板子：`skill/pitfalls/pynq_serial_console.md`
- 网络配置：`skill/pitfalls/pynq_direct_ethernet_windows.md`
