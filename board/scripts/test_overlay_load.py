"""最小 Overlay 加载测试 —— 验证板子能不能把 .bit 加载起来。

用法（在 PYNQ 板子上跑）：

    env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 test_overlay_load.py

这两处都不能省，缺一个就会报 `No Devices Found`：
  1. 必须用 venv 里的 python —— 系统 /usr/bin/python3 里没有 pynq
  2. 必须带上 XILINX_XRT=/usr —— 登录 shell 会自动设这个变量，SSH 直连跑命令不会

换成自己的 overlay：把 BITFILE 改成你的 .bit 路径（.hwh 必须同目录、同文件名）。
"""
import sys

BITFILE = "fir_accel2.bit"

print("python", sys.version.split()[0])
try:
    import pynq

    print("pynq", pynq.__version__)
except Exception as e:
    print("no pynq:", e)
    raise SystemExit(1)

from pynq import Overlay


def try_load(**kw):
    try:
        ol = Overlay(BITFILE, **kw)
        print("== LOADED OK ==", kw)
        return ol
    except Exception as e:
        print("== FAIL ==", kw, type(e).__name__, e)
        return None


ol = try_load()
if ol is None:
    # 只有 PYNQ 与生成 .bit 的 Vivado 版本不匹配时才需要这个开关
    ol = try_load(ignore_version=True)

if ol is not None:
    print("--- ip_dict ---")
    for name, info in ol.ip_dict.items():
        addr = info.get("phys_addr") if isinstance(info, dict) else "?"
        print("  %-30s %s" % (name, addr))
    print("结论: overlay 加载成功")
else:
    print("结论: 两种方式都加载失败")
