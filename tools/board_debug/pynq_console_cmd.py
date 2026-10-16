# ⚠ 2026-10-16 收尾补:主机侧。往板子串口发一条命令并把回显抓回来(靠 __BEGIN__/__END__ 定界)。
#   用法:python pynq_console_cmd.py "命令" [等几秒]
#   往串口发的命令一律写成**单行** —— 跨行结构(for ... do ... done)里的反斜杠会被吃掉一层,
#   shell 停在续行提示符上,后面几条全被当成循环体吞掉,表现是"发了命令一条输出都没有"。
#   注意:它和 pynq_serial_capture.py 抢同一个 COM 句柄,同一个时刻只能开一个。
"""Send a command to the PYNQ-Z2 serial console and print what comes back.

Usage: python pynq_console_cmd.py "<shell command>" [seconds]
"""
import sys
import time

import serial

PORT = "COM6"
cmd = sys.argv[1]
wait = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

sp = serial.Serial(PORT, 115200, timeout=0.25)
time.sleep(0.4)
sp.reset_input_buffer()

sp.write(b"\n")                      # make sure a fresh prompt is printed
time.sleep(0.6)
sp.write(("echo __BEGIN__; " + cmd + "\necho __END__\n").encode())
sp.flush()

t0 = time.time()
buf = b""
while time.time() - t0 < wait:
    chunk = sp.read(4096)
    if chunk:
        buf += chunk
        if b"__END__" in buf:
            break
sp.close()

text = buf.decode("utf-8", "replace")
start = text.find("__BEGIN__")
end = text.find("__END__")
if start >= 0 and end > start:
    text = text[start + len("__BEGIN__"):end]
print(text.strip())
