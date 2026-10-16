# ⚠ 2026-10-16 收尾补:主机侧。抓板子串口,能在板子被断电、串口设备消失又重新枚举
#   之后自己接回来 —— 这正是抓死机现场需要的(`python pynq_console_cmd.py` 做不到)。
#   用法:python pynq_serial_capture.py COM6 21600 <日志路径>
#   非要读串口的时候**必须开着它**,否则 panic_print 打出来也没人接。
#   注意:它和 pynq_console_cmd.py 抢同一个 COM 句柄,同一时刻只能开一个。
"""Capture the PYNQ-Z2 UART console, surviving the device disappearing and
re-enumerating (which happens when the board is power-cycled).

Read-only. Appends everything to a log file.
"""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
OUT = sys.argv[3] if len(sys.argv) > 3 else "pynq_console.log"

t0 = time.time()
total = 0
fh = open(OUT, "ab")
fh.write(b"\n===== capture start %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S").encode())
while time.time() - t0 < SECONDS:
    try:
        sp = serial.Serial(PORT, 115200, timeout=0.3)
    except Exception as exc:
        print("waiting for %s (%s)" % (PORT, exc), flush=True)
        time.sleep(1.0)
        continue
    print("attached to %s at t=%.1fs" % (PORT, time.time() - t0), flush=True)
    try:
        while time.time() - t0 < SECONDS:
            chunk = sp.read(4096)
            if chunk:
                fh.write(chunk)
                fh.flush()
                total += len(chunk)
    except Exception as exc:
        print("port dropped at t=%.1fs: %s" % (time.time() - t0, exc), flush=True)
    finally:
        try:
            sp.close()
        except Exception:
            pass
    time.sleep(0.5)
fh.close()
print("captured %d bytes in %.0f s" % (total, time.time() - t0), flush=True)
