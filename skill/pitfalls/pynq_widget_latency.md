---
name: pynq-widget-latency
description: >
  Diagnose "the display lags behind the hardware" on a PYNQ-Z2 Jupyter notebook that
  drives a live signal chain with ipywidgets. Use when a meter or readout trails the
  sliders by seconds even though the audio has already changed, when one widget's
  update rate seems to throttle another's, when CPU affinity was already pinned and the
  lag remains, or when a cell re-run leaves background threads updating widgets that no
  longer exist.
license: MIT
compatibility: PYNQ-Z2 image 2.7.0, Jupyter 5.x, ipywidgets 7.x
metadata:
  version: "1.0.0"
  updated: "2026-10-18"
---

# 界面滞后:先切链路,再猜层

板上实时通路已经跑通、音频也确实变了,但界面上的表针/读数要过几秒才跟上。
实测于 Pynq-Z2 / PYNQ 2.7.0 / Jupyter 5.x,2026-10-18。

## 症状

拖滑杆 → 耳机里立刻听到变化,但同一个页面上的**另一个**控件(表针、读数、进度)
要好几秒才到位。看起来完全不合逻辑:一个控件慢,另一个快。

## 先量,不要猜

**1. 量"程序 → 文件"这一段。**
在板上挂一个 25 Hz 的监视器,同时记:

- 命令文件(`/dev/shm/xxx_tune.json`)的 `st_mtime_ns` 和自增 `seq`;
- 状态文件(`/dev/shm/xxx_status.json`)的 `t`、`n`、目标值、实际值。

判据:状态文件里"实际值第一次等于目标值"的时刻 − 命令文件 mtime。
**这一段是几十毫秒就说明瓶颈在浏览器那一侧**,不用再查 CPU、不用再查驱动。

实测(48 次快速拖动):中位 32 ms、p90 56 ms、最慢 109 ms。

**2. 找一条能一刀切开链路的判据。**
新开一个单元格,只跑一句把某个控件停掉,别的都不动:

```python
SCOPE["stop"].set()      # 只停波形,不停表针
```

如果另一个控件**立刻**变实时 —— 那就是被停掉的那个控件堵住了通道。
反过来恢复它,滞后回来,判据成立。

## 机理

ipywidgets 的每个 `value` 赋值都会产生一条发往浏览器的消息,浏览器**按顺序**
处理。一个控件的 `value` 是一大段 HTML 字符串时,每条消息都要重新解析 + 重新排版。

10 Hz × 两个面板 × 每根线 240 点,拼出来的 SVG 字符串足够大,
浏览器排不过来 → 队列越积越晚 → **排在同一个队列里的其他控件一起晚**。

## 修法

1. **降帧率、降点数。** 10 Hz → 4 Hz、240 → 120 点,负担降到约 1/8。
   波形看起来仍然够用。
2. **不要每帧重建整段 HTML。** 能只改数值就别重建结构;能用固定长度的 SVG
   路径字符串就别重新拼 head/属性。
3. **重跑单元格要显式收掉旧线程。** 每次重跑都 `Event()` + `Thread()` 的话,
   旧线程收不到 `stop`,会继续按原频率往**已经不在页面上**的 widget 写,
   白烧 CPU 而且加剧队列积压。用模块级的字典复用同一个 `Event`:

```python
try:
    SCOPE
except NameError:
    SCOPE = {"stop": threading.Event(), "th": None}

SCOPE["stop"].set()
if SCOPE["th"] is not None:
    SCOPE["th"].join(timeout=1.0)
SCOPE["stop"].clear()
SCOPE["th"] = threading.Thread(target=_scope_watch, daemon=True)
SCOPE["th"].start()
```

判据:内核里刷新线程的个数应该正好等于设计值。多出来的就是没停掉的旧线程。

## 别踩的两条

- **"块号每帧涨 10"不能证明它是新的。** 100 块/秒 ÷ 10 Hz 本来就该涨 10;
  队列堵住时它照样涨 10。要证明新鲜,得拿它和外部时间基准(状态文件的 `t`)比。
- **`os.sched_setaffinity(0, {1})` 只钉当前这一个线程。** Jupyter 内核的
  ZeroMQ I/O 线程、Jupyter 服务进程的线程都还留在原来的核上。
  要按 `/proc/<pid>/task/*` 逐个钉:

```python
for tid in os.listdir("/proc/%d/task" % pid):
    os.sched_setaffinity(int(tid), {1})
```

  线程**创建之前**对进程设的亲和性不会追溯生效;新线程继承创建者的亲和性。
  板上实测:被实时搬样线程饿住的 CPU0 上,主线程 5 秒只被调度 **4 次**,
  最大间隔 **2.742 秒**;钉到 CPU1 后同一窗口 **393 次**。

- **CPU 亲和性是真问题,但可能不是这次的问题。** 两层可以叠加:
  先修了 CPU,仍然慢,再查显示那一路。**每修一层都要重新量一次**,不能默认"修了就好了"。

## 环境限制

- 板上通常没开 `CONFIG_SCHEDSTATS`,`/proc/<pid>/task/*/schedstat` 读不到,
  只能用 `stat` 里的 utime/stime 差分。
- 板上不一定有 `jupyter_client`(连不上外网),想从第二个客户端旁听 IOPub
  来量"消息发出去多久到浏览器"这条路走不通,只能靠停控件的排除法。
