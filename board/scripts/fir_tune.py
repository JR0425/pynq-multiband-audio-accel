# -*- coding: utf-8 -*-
"""实时调参的那条链路：界面 ←→ 跑着的那条实时程序。

为什么要用两个文件，而不是共享内存 / socket / 直接读寄存器
----------------------------------------------------------
`fir_live.py` 的时序是抠出来的（看它开头那三个坑）：**搬样那条线程零余量**，
过核那条每块 10 ms 的预算里只剩 7 ms。任何跨进程机制在这里必须满足两条：

  ① 界面的死活不能影响音频 —— 浏览器关掉、Jupyter 卡一下，声音照跑
  ② 每块只花几十微秒

文件正好：读一次是几十微秒；跑的那一半只读、界面只写，
两边不共享任何活的东西，谁也不等谁。

    /dev/shm/fir_tune.json      界面写 → fir_live.py 读    （要拧的旋钮）
    /dev/shm/fir_status.json    fir_live.py 写 → 界面读    （现在的状态 + 电平）

⚠️ 为什么在 /dev/shm 而不是 /tmp（2026-10-10 实测，这是本项目最贵的一个坑）
--------------------------------------------------------------------------
**PYNQ-Z2 上 `/tmp` 不在内存盘上，在 SD 卡上。** 同样一个 300 字节的 JSON：

    写法                          /tmp（SD 卡）   /dev/shm（tmpfs）
    ----------------------------------------------------------------
    先写 .tmp 再 os.replace            2168 µs         902 µs
    直接 open + write                  2964 µs         811 µs
    常开 fd 只 os.write                  33 µs          23 µs   ← 用它

20 Hz 写状态，按 2168 µs 算就是每秒 43 ms 卡在文件系统里。实测后果：
**实时那条每 30 秒多丢 17 块音**（基线只丢 6 块）—— 耳机里大约每 1.3 秒"咔"一下。

根因不是"写文件慢"，是**写文件的那一下把过核线程按住 2 ms**，
而搬样那条线程是一点余量都没有的（它就是 I2S 时钟本身，晚 100 µs 就丢采样）。

所以两条一起改：**换到 /dev/shm**（内存盘，不碰 SD 卡，顺带不磨卡）+ **常开 fd 只 write**。

代价：状态文件不再有"先写 .tmp 再换名"的原子性，理论上读的一方可能撞见
写了一半的内容 —— 那边 `_read()` 返回 None，界面保留上一帧的值，100 ms 后自愈。
23 µs 的窗口撞上 100 ms 一次的轮询，概率可以忽略；省下的 2 ms 是实打实的。

旋钮文件那一边仍然用原子写（它一次要写好几百毫秒才写一次，贵不到哪去），
所以读的一边**不能常开 fd** —— 换名换的是 inode，常开 fd 会一直读旧文件。

⚠️ 两个文件只在本次开机有效，重启就回默认。旋钮是现场拧的，
   不该落到盘上变成"上次那一组"。

用法
----
跑着的那一半（`fir_live.py`）：

    from fir_tune import read_tune, write_status
    d = read_tune()                 # 没有 / 坏了都返回 None，调用方保持原值
    write_status({"n": 3001, ...})

界面那一半（notebook）：

    from fir_tune import write_tune, read_status
    write_tune({"seq": 7, "gain_db": [-6, 0, 6, 12]})
"""

import json
import os

TUNE_PATH = os.environ.get("FIR_TUNE", "/dev/shm/fir_tune.json")
STATUS_PATH = os.environ.get("FIR_STATUS", "/dev/shm/fir_status.json")


def _read(path):
    """读不回来就返回 None，**不要抛异常**。

    界面还没起、文件被删了、刚写一半，都是正常情况；
    这时调用方保持上一次的值继续跑就行 —— 调参链路断了不该把音频带下水。
    """
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _atomic_write(path, obj):
    """先写同目录下的 .tmp，再 os.replace 换名 —— 读的一方永远看到完整的一份。

    只有**旋钮文件**用它（写得少）。状态文件走下面的常开 fd，理由见文件开头。
    """
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def read_tune():
    return _read(TUNE_PATH)


def write_tune(obj):
    _atomic_write(TUNE_PATH, obj)


def read_status():
    return _read(STATUS_PATH)


_status_fd = [None]


def write_status(obj):
    """写状态文件。**这是整条链路里唯一要花钱的地方，把它写便宜就便宜了。**

    常开 fd + `os.write`，不换名。实测 23 µs（/dev/shm），
    对比 `.tmp`+`replace` 的 902 µs、以及 SD 卡上的 2168 µs —— 见文件开头那张表。

    `ftruncate` 不能省：这一帧比上一帧短的话，尾巴会留在文件里，
    拼出来的就不是合法 JSON 了（界面那边会静默地一直读到 None）。
    """
    s = json.dumps(obj).encode("utf-8")
    if _status_fd[0] is None:
        _status_fd[0] = os.open(STATUS_PATH,
                                os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    fd = _status_fd[0]
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, s)
    os.ftruncate(fd, len(s))


def clear_status():
    """开跑之前先把上一次的状态文件删掉。

    不删的话，界面在"还没跑起来"的那几秒里会读到**上一趟的数字**，
    看起来像"已经在跑了、只是数没动" —— 最坑的一种假象。
    """
    if _status_fd[0] is not None:          # 换了名之后旧 fd 就没用了
        try:
            os.close(_status_fd[0])
        except OSError:
            pass
        _status_fd[0] = None
    try:
        os.unlink(STATUS_PATH)
    except OSError:
        pass
