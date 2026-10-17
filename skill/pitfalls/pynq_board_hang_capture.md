---
name: pynq-board-hang-capture
description: >
  Capture and interpret a PYNQ-Z2 board hang: telling a hang from a power-off,
  getting the stuck core's call stack out of a board where almost every debugging
  facility is compiled out (no hung_task, no softlockup, no sysrq, no ftrace,
  no /proc/<pid>/stack), reading an RCU stall report, and running A/B comparisons
  against a hang. Use when the board stops answering, when the serial console goes
  silent, when a run "sometimes finishes and sometimes dies", or when writing a
  soak script for the board.
license: MIT
compatibility: PYNQ-Z2 image 2.7.0, kernel 5.4.0-xilinx-v2020.2 (armv7l, PREEMPT)
metadata:
  version: "1.2.0"
  updated: "2026-10-17"
---

# PYNQ-Z2 死机现场的抓取与判读

板子跑起来会挂，挂完没有任何输出。这份笔记记的是**怎么把现场留下来**、
**怎么读它**，以及在一次挂都可能不是确定性的情况下**怎么做对照实验**。

实测：PYNQ-Z2 + PYNQ 2.7.0 镜像 + Vivado 2020.2，2026-10-15 / 10-16。

## 什么时候用

- 板子不回答 ping / SSH，想知道它是死了还是断电了
- 串口一个字都不吐，需要现场
- 同一条命令**有时跑完、有时挂**，要判某个配置有没有影响
- 要给板子写 soak / 压力脚本

## 什么时候别用

- 只是想重跑一个脚本 —— 先看是不是脚本自己的报错
- 板子还在跑实时管线时**不要**往上面打重命令（`journalctl -k -b -1` 这种猛读 SD 卡的，
  会和管线抢 I/O，把那一轮数据污染掉）

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-10-16 | 初版。`panic_print=31` 的路子、断电/死机的判据、A/B 对齐规矩 |
| 1.1.0 | 2026-10-17 | 补第七节：RCU-stall 的根因（`ksoftirqd` 饿死）与修法（提权到 FIFO 50）；补"假 PASS"判据 |
| 1.2.0 | 2026-10-17 | 600 秒复测发现只提 `ksoftirqd` 不够：`kworker/0:1` 也会饿死。修法扩展到 `kworker/[01]:*` |

## 一、先判「断电」还是「死机」

**两个信号一起看，别只看一个。**

| | 电脑侧网口 | COM6 串口设备 |
|---|---|---|
| **断电**（或人主动关机） | **Disconnected** | **整个消失**（FT2232 随板子一起掉） |
| **死机** | **Connected / 1 Gbps** | 设备还在，但**一个字都不吐** |

网口链路还在一行可以用 PowerShell 的 `Get-NetAdapter` 看。
PHY 是自协商的，**不需要 Zynq 配置网口**，所以 PS 停了链路也照样在。

再加一条更强的判据：

```
arp -a
```

里面**连板子的 IP 条目都没有** ⇒ ARP 是内核答的，内核已经整个停了。
光 ping 不通只说明网络路径断了，说明不了内核死活。

Windows 上还要注意认对网卡：真实以太网卡上才有 `192.168.2.1`，
虚拟网卡（Sangfor SSL VPN 之类）永远是 Disconnected，别看错那一行。

## 二、这张板子上哪些调试设施没有

查过 `/proc/config.gz`（2026-10-16），下面是**没编**的：

| 想要的东西 | 为什么用不了 |
|---|---|
| `CONFIG_DETECT_HUNG_TASK` | 连 `/proc/sys/kernel/hung_task_timeout_secs` 都不存在，抓不到 D 状态任务的栈 |
| `CONFIG_SOFTLOCKUP_DETECTOR` | 没有 soft lockup 报警 |
| `CONFIG_MAGIC_SYSRQ` | **串口发 BREAK 也不行** |
| `CONFIG_STACKTRACE` | `/proc/<pid>/stack`、`/proc/self/stack` 都不存在 |
| `SCHED_DEBUG` | 没有 `/proc/sched_debug`，看不到每核 `rt_rq` 的账本 |
| ftrace | 没有 |

已编的：`CONFIG_PREEMPT=y`、`CONFIG_PREEMPT_RCU=y`、`CONFIG_NO_HZ=y`、
`CONFIG_HZ=100`、`CONFIG_IRQ_FORCED_THREADING=y`、`CONFIG_RT_GROUP_SCHED=y`、
`CONFIG_ARM_UNWIND=y`。

**结论：能用的只有 `panic` 和 `rcu_cpu_stall*` 这一族。**

## 三、唯一还能拿到栈的路：`panic_print`

`panic_on_rcu_stall` 会让板子在检测到 RCU stall 时直接 panic（这是好事，否则它只是
静静卡住）。但它在 `rcu_dump_cpu_stacks()` **之前** panic，**所以卡住那颗核的栈永远
打不出来** —— 这就是"录到 5 次 panic、0 次栈"的原因。

`panic_print` 走的是 panic 处理**早期**的 `panic_print_sys_info()`，不受那条顺序影响：

```sh
sudo sysctl -w kernel.panic_print=31     # 位0=任务状态+内核栈(等价 sysrq-t) 位1=内存 位2=寄存器 位8=时间戳
```

实测一次打出 **1468 行 / 633 个栈帧**（转储正文；`data/results/board_hang_rcu_stall_dump_20261016.txt`）。板上 31 写得进去（位 0/1/2/8 都支持）。

两条配套的：

```sh
sudo sysctl -w kernel.panic_on_rcu_stall=1   # 不设它，板子只是静悄悄卡住
sudo sysctl -w kernel.panic=15               # panic 后 15 秒自己重启
```

**必须同时开着串口录制**，否则打出来也没人接：

```sh
python tools/pynq_serial_capture.py COM6 21600 <日志路径>
```

`panic_print` 只改"panic 时打什么"，**不改任何行为** ⇒ 可以一直开着，
做 A/B 的每一臂都带上，顺手给下一次死亡留现场。

> 反例教训：这块板上一度被判定"没有任何能拿到栈的路"，于是转去靠猜。
> 那个判断是**顺着"这些设施没编"下的，没去读 panic 那条路径的实现顺序**。
> 一个"没有路可走"的否定判断，只要还有一条路的实现细节没读，就只是"没找到"。

## 四、怎么读一份 RCU stall 报告

```
rcu: INFO: rcu_preempt self-detected stall on CPU
rcu: 	0-....: (999 ticks this GP) idle=b02/1/0x40000002 softirq=10908/10908 fqs=500
Kernel panic - not syncing: RCU Stall
CPU1: stopping
```

- **`0-....`** 开头那个数字是被卡住的核号。另一颗核通常停在 `do_idle`（空闲），
  它的栈是噪音，别拿它当线索
- **`softirq=a/b` 两个数相等** ⇒ 整个宽限期里这颗核**一个 RCU 软中断都没跑过**
- **`fqs`** 和超时成正比，**这是事后认"当时超时是多少"的唯一依据**：
  10 秒 → `fqs≈500`，60 秒 → `fqs≈3000`
- 往下的 `task ... PC stack ... father` 才是 `panic_print` 打出来的全任务表

看全任务表时，找两样东西：

| 找什么 | 说明什么 |
|---|---|
| `ksoftirqd/N`、`kworker/N:*` 的状态是 `R running task` 而栈停在 `__schedule` | **可运行却一直轮不上**；正常应该是 `S` |
| **两颗核的同一个内核线程状态不一样** | 这是最硬的线索。比如 `ksoftirqd/0` 是 `R`、`ksoftirqd/1` 是 `S`，说明问题只在一颗核上 |
| 用户态线程的 CPSR 低 5 位 | `0x10` = User 模式 ⇒ 它是在用户态烧 CPU，不是卡在内核里 |

### 改 RCU stall 的判定时间

默认 **60 秒**，只能在 `/sys/module/rcupdate/parameters/rcu_cpu_stall_timeout` 写
（`/proc/sys/kernel/` 下没有这个名字），kernel 自己会夹到 ≥3 秒：

```sh
sudo sh -c 'echo 10 > /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout'
```

抓现场时可以压到 10 秒让"挂→报→panic→重启"从约 100 秒缩到约 40 秒。
但 **做 A/B 对照时必须留 60 秒默认值** —— 10 秒会让"差一点但没挂"的轮次也变成挂，
比较就不公平了。

## 五、做对照实验的规矩

这块板的挂**很可能是概率性的**（同一套命令既挂过也活过），所以：

1. **对齐每臂的起跑状态。** 用 `/proc/uptime` 对齐，不要用墙上时间 ——
   板子的 RTC 是假的（默认停在镜像制作那天）。
   每臂起码等到开机满几分钟再跑，原因是**刚开机时 systemd / journald / udev 还在猛写 SD**：
   实测有一次起跑时 `mmcblk0` 的 `time_writing` 累计 86.7 秒，而 uptime 才 58 秒。
   不对齐的话，"关掉某项设置"和"当时板子刚开机正忙"就混在一起，这一轮判不了。
2. **用"能撑多久"判，不用"挂了几次"判。** 挂的基准概率可能约每 30~60 秒一次，
   跑 10 秒就挂和跑 10 分钟才挂不是一回事。
3. **一定要有"什么都不跑"的对照组。** 起一个 `IDLE` 臂：不加载 overlay、不碰 PL、
   只让板子闲着几百秒。它活而管线挂 ⇒ 锁定在管线/PL 这条路上；
   它也挂 ⇒ 跟代码一行都无关。
4. **挂了的臂后面要么等它自己回来，要么收工。** 别拿着一条死板子把后面的配置
   一个个"跑"成假 HANG —— 那种记录会把账算歪。
   判断"板子回来没有"要用 `uptime` 能不能读到，并且**别把读失败（`< 0`）
   当成"等到了"**；那样会在死板子上继续往下跑。
5. **每臂起跑前把当前设置记到主机侧。** 板子一挂，板上那份日志跟着没，
   `panic_print` / `panic_on_rcu_stall` 之类的设置就永远读不回来了。
6. **别在 soak 跑的时候往板子上打别的重命令**（见「什么时候别用」）。
7. **两个串口程序不能同时开。** `pynq_serial_capture.py` 和 `pynq_console_cmd.py`
   会抢同一个 COM 句柄，第二个直接报错。要发命令就先停录制。
8. **板上的存活判断加 `sudo`。** 板上跑的是 root 进程时，
   `kill -0 $PID` 不加 `sudo` 会因"没权限"返回非零 —— 那样这条日志**永远**报
   `alive=n`，看着像"进程没起来"，其实什么也没测到。

## 六、这块板上已知能挂死的两种形态

| 形态 | 串口 | panic | 怎么回来 |
|---|---|---|---|
| RCU stall → panic | 有完整转储 | 有 | 约 60 秒后自己重启 |
| **静默硬死** | **一个字都没有** | **没有** | **要人按电源** |

第二种目前**没有解释**，唯一不矛盾的猜想是"PL / AXI 读不返回，两颗核一起卡在总线
访问上" —— 那样连负责报 stall 的内核线程自己都跑不上，所以串口全静。
（旁证：Zynq-7000 的 `M_AXI_GP` 没有 AXI 超时，一次不返回的读就是永久性的。）

**不要**因为看到了第一种的机制就宣布它是"挂的原因"。两者是分开的问题：
一份转储能解释**怎么死的**，不等于能解释**为什么会死**。

**硬件看门狗这条自救路在这块板上走不通（已实测判死）。**
`cadence_wdt` 驱动能 probe（dmesg 有 `Xilinx Watchdog Timer with timeout 10s`），
`/sys/class/watchdog/watchdog0/` 底下也没有标准的 `state`/`timeout`/`nowayout`
属性，实测武装之后停喂近 380 秒一次不复位。
要救只能绕开驱动直接写 Zynq-7000 的 SWDT 寄存器（需 `/dev/mem` + 确认 CR/MR/RESTART
偏移），而且即使做成了也只是"10 秒后自己回来"，缓解不了演示中间反复黑屏这件事。

## 七、RCU stall 的根因与修法（2026-10-17 定案）

### 根因

`fir_live.py` 的搬样线程是 `SCHED_FIFO 20`，`duplex_block()` 里是纯 MMIO 轮询，
**按设计永不阻塞**。同一个核上的 `ksoftirqd`（以及 `kworker/*`）是普通 CFS 类，
实时任务可运行时永远排不上 → CPU0 的 RCU 静默态报不上去 → 60 秒后判 stall → panic。

三份转储同型：`python3`（搬样线程）在 CPU0 是 `R running task`，
`ksoftirqd/0`、`kworker/0:1`、`kworker/0:3H` 也都是 `R` 但没上过 CPU。

### 判据上的坑：90 秒的臂会有"假 PASS"

stall 要连续 60 秒才触发 panic。如果饿死发生在第 30 秒之后，程序在 90 秒先退出、
把 CPU0 还给内核，stall 计时器就被清零 —— 板子不 panic，`RESULT=done` 也会返回，
但中间那 60 秒网络已经不通。**判活必须同时看拉取器 `.stamp` 有没有连续超时。**
round9 `N_short` #2 就是这样：第 37 秒起失联 64 秒，却被记成 PASS。

### 修法

把 `ksoftirqd/0`、`ksoftirqd/1` **和 `kworker/[01]:*`** 提到 `SCHED_FIFO 50`
（高于搬样线程的 20）：

```sh
sudo chrt -f -p 50 $(pgrep -f '^ksoftirqd/0$' | head -1)
sudo chrt -f -p 50 $(pgrep -f '^ksoftirqd/1$' | head -1)
for pid in $(pgrep -f '^kworker/[01]:'); do sudo chrt -f -p 50 $pid; done
```

需要时它们能抢占搬样线程处理软中断；不需要时搬样线程仍独占 CPU。
实测 300 秒实时全开：**活着跑完，丢块 61（0.20 块/秒），实时 0.998x**。
开 SSH 拉取器的正式轮次（round11，2×300 秒）也全活，丢块 220/226，实时 0.993x ——
多出来的很可能来自拉取器自己的网络负载；演示时状态文件本地读，不走 SSH。

**只提 `ksoftirqd` 不够（2026-10-17 round12b）。** 600 秒那一臂跑到约 390 秒挂了，
转储里 `ksoftirqd/0` 已经是 `S`，但 RCU 点名
`Tasks blocked on level-0 rcu_node (CPUs 0-1): P21`，而 P21 是 `kworker/0:1 R`。
kworker 也是 CFS，同样会被 FIFO 20 的搬样线程永久饿死；它在一次 RCU
读端临界区里被抢占后，就再也报不出静默态。所以修法必须同时覆盖 kworker。

对比两种"能活"的路子：

| 配置 | 300 秒 | 丢块/300 秒 |
|---|---|---|
| 实时开，不提权 | 挂 | — |
| 实时关（`LIVE_RT_IO=0 LIVE_RT_DSP=0`） | 活 | 552~567 |
| **实时开，`ksoftirqd` 提权（手工）** | **活** | **61** |
| **实时开，`ksoftirqd` 提权（带拉取器）** | **活** | **220~226** |

600 秒：只提 `ksoftirqd` 会挂（round12b，约 390 秒）；同时提 `kworker/[01]:*` 后
**600 秒全活**（round13：59478 块、丢 539、0.991x，拉取器 212 次全应声）。
workqueue 运行时会按需新建 kworker，所以开机扫描之外还要一个 30 秒重扫 timer
（`ksoftirqd-rt-refresh.timer`，同一个脚本）。

已做成开机服务，装一次即可（重刷 SD 卡后要重装）：

```sh
sudo sh board/scripts/install_ksoftirqd_rt.sh
systemctl is-active ksoftirqd-rt.service       # active
```

脚本：`board/scripts/ksoftirqd_rt.sh`、`board/scripts/install_ksoftirqd_rt.sh`。
完整账本：`data/results/board_hang_evidence_20261017.md`。

### 仍未解释的

第六节那种**静默硬死**没有复现。round7 之后所有"对齐起跑"的关实时臂（4 次）都活着，
所以它暂时被当成 up=44、开机风暴未过的离群点；但不能断言第二种死法不存在。

## 相关

- 断电与关机、卡有没有被拔坏：`skill/pitfalls/pynq_shutdown_and_power.md`
- 串口控制台：`skill/pitfalls/pynq_serial_console.md`
- 连通性自检：`skill/checkers/check_board_connection.bat`
- 可复用的脚本：`tools/board_debug/`（README 里写明哪些在主机侧跑、哪些在板上跑）
