# 板子死机 2026-10-15 ~ 10-16 现场账本

本文件是这两天所有死机实验的**原始读数汇总**。配套两份串口转储（原始文本，一个字节没改）：

- `board_hang_rcu_stall_dump_20261016.txt` —— round6 `A_dump` 那一轮
- `board_hang_rcu_stall_dump_20261016_r7.txt` —— round7 `N_short` 那一轮

**一句话现状**：知道「为什么会 panic」了，**不知道「为什么会挂」**。
panic 的机制抓到了两份完整转储；但把实时优先级关掉照样挂，而且换成了没有输出的静默硬死。
所以「关掉实时就能救」这条**作废**。

---

## 一、逐轮记账

每一轮的原始主机侧日志在本地 `tools/_tmp/roundN/round_host.log`（未入库，见文末复现命令）。

| 轮 | 配置 | 起跑 up | 板子最后应声 | 结果 | 转储 | 备注 |
|---|---|---|---|---|---|---|
| 2 | `A_notebook` #1 | 148 | 169 | **PASS** | — | 同一条命令，这一轮活着 |
| 2 | `D_nopin` #1 | 171 | — | HANG | — | SSH `ConnectionReset`，62 秒后自己回来 |
| 2 | `A_notebook` #2 | 45 | — | HANG | — | SSH timeout，再没回来，**要人按电源** |
| 3 | — | — | — | 未开跑 | — | 板子起不来，`wait_up` 等 10 分钟收工 |
| 4 | `K_clean` #1 | 671 | ~22:29:09 | HANG | — | 板上**一个黑匣子都不开**，照样挂 |
| 4 | `N_clean` #1 | 44 | 59.8 | HANG | — | 起跑时才开机 44 秒 |
| 5 | `P_sdquiet` #1 | 78 | 90.2 | HANG | — | 停 journald + 停 daily 定时器，照样挂 |
| 5 | `Q_stack` #1 | 67 | 74.0 | HANG | — | 照样挂 |
| 6 | `A_dump` #1 | 282 | 314.4 | HANG | **✓** | `DUMP=1` + `FASTSTALL=1`，正文 1468 行 / 633 帧到手 |
| 7 | `N_short` #1 | 526 | 535.6 | HANG | **✓** | 基线（实时开着），复现同一形态 |
| 7 | `R_rtoff` #1 | 44 | 58.0 | HANG | **✗** | `LIVE_RT_IO=0 LIVE_RT_DSP=0`；**静默硬死**，要人按电源 |
| 8 | `Z_idle` / `N_long` | — | — | 未开跑 | — | 板子关着，没上电 |

### 两条必须连起来看的事

**① 「起跑 up」这个数列本身是个警告。**
round4/5/7 的每一臂，都是**上一臂挂掉、板子 panic 重启之后**紧接着开跑的（44 / 78 / 67 / 44 秒）。
开机不到 90 秒时 systemd / journald / udev 还在猛写 SD
（round7 `R_rtoff` 起跑时：`mmcblk0` 的 `time_writing` 累计 86.7 秒，而 uptime 才 58 秒），
所以「关掉某项设置」和「当时 SD 正忙」在这几轮里**混在一起，分不开**。
`next_round.py` 的 `NEXT_SETTLE` 就是干这个的，round7 那轮忘了设（=0），**所以那一轮判不了**。

**② 起跑在「稳定板子」上的三臂，全都挂了。** 这一条是硬的：

| 配置 | 起跑 up | 结果 |
|---|---|---|
| `K_clean` | 671 | HANG |
| `A_dump` | 282 | HANG |
| `N_short` | 526 | HANG |

⇒ 不是「刚开机才挂」。板子开机 5~11 分钟、load 0.04 的状态下，跑管线照样挂。

---

## 二、panic 现场的机制（两份转储互相复现）

### 直接读数（`board_hang_rcu_stall_dump_20261016.txt`）

```
 31| rcu: 	0-....: (999 ticks this GP) idle=b02/1/0x40000002 softirq=10908/10908 fqs=500
 32| Kernel panic - not syncing: RCU Stall
 33| CPU1: stopping
...
117| ksoftirqd/0     R  running task        0     9      2 0x00000000
118| [<c08e2ef4>] (__schedule) from [<c08e30f8>] (schedule+0xb4/0xec)
...
172| ksoftirqd/1     S    0    15      2 0x00000000
...
1203| python3         R  running task        0   922    880 0x00000000
...
1207| [<c011b89c>] (panic) from [<c0166098>] (panic_on_rcu_stall+0x1c/0x24)
1218| [<c0476768>] (gic_handle_irq) from [<c0101e70>] (__irq_usr+0x50/0x80)
1222| dfe0: 009577a4 b412872c 0095689c b443d5f8 20070030 ffffffff
```

逐条读：

- **第 31 行 `fqs=500`** ⇒ 这一轮的 stall 超时是 **10 秒**（`FASTSTALL=1` 改过）。
  60 秒默认值时同一个位置是 `fqs≈3000`（10-15 那次 `console_capture_w3.log` 里是 `fqs=2998`）。
  这是事后判断「那一轮超时是多少」的唯一依据。
- **两份转储的差别要如实看**：round6 那份的 `softirq=10908/10908`（两个数相等 ⇒ 整个
  宽限期里一次 RCU 软中断都没跑）；round7 那份是 `softirq=8573/8575`（**跑过 2 次**）。
  方向一致、程度不同 —— 能证的是"CPU0 被饿到静默态报不上去"，不是"一次都跑不动"。
- **第 117 vs 172 行**：`ksoftirqd/0` 是 **`R running task`**（可运行，但栈停在 `__schedule`，
  一直轮不上），`ksoftirqd/1` 是 `S`（正常睡着）。**两颗核不一样，这是全案的支点。**
- **第 1203 / 1222 行**：`python3` pid **922** 是搬样线程。它被 tick 中断打断时
  `__irq_usr` + CPSR = `0x20070030`，低 5 位 `0x10` = **User 模式**
  ⇒ 它在**用户态**烧着 CPU0，不是卡在内核里。
- **第 1207 行**：panic 是从 `panic_on_rcu_stall` 直接进来的，
  `rcu_dump_cpu_stacks()` 根本没走到 —— 这就是为什么以前「录到 5 次 panic、0 次栈」。
- 同一份转储里 CPU0 的 `kworker`（含两个标 `mmc_complete` 的）也全是 `R` 且停在 `__schedule`。

### 机制

`fir_live.py` 把搬样线程设成 **`SCHED_FIFO` 20 且永不阻塞**
（`fir_live_board.py:366` 的 `_pin(0, RT_IO)`，零余量空转轮询 PL 状态寄存器），钉在 CPU0。
`ksoftirqd/N` 和 `kworker/*` 是**普通 CFS 类**（nice 0；`*H` 那个是 nice -20），
任何 RT 线程都压得过它们。唯一的护栏是 RT 限流，实测是**默认值**：

```
sched_rt_runtime_us=950000     # 每秒给非 RT 留 5%
sched_rt_period_us=1000000
```

而这 5% 被 nice -20 的 mmc/块层 worker 吃掉（CFS 权重 88761 : 1024 ≈ 99 : 1），
nice 0 的 `ksoftirqd/0` 抢到的≈0 → CPU0 的 RCU 静默态永远报不上去 → 60 秒后 stall → panic。

**CPU1 永远不 stall 是关键对照**：板上每个设备中断都落在 CPU0
（`/proc/interrupts` 里 xuartps 26 / eth0 28 / mmc0 29 的 CPU1 那列恒 0），
所以 CPU1 没有 nice -20 的竞争者，那 5% 归它自己的 `ksoftirqd/1`。

### ⚠ 这不是「挂」的成因

round7 的 `R_rtoff`（实时关掉）**也挂了**，而且换了一种死法：
串口一个字都没有、无 panic、`arp -a` 里连 192.168.2.99 的条目都没了
（ARP 是内核答的 ⇒ 内核整个停了）、网口 LinkSpeed 仍 1 Gbps（⇒ 死机不是断电）、
3 分钟不回来、**要人按电源**。

⇒ RT 饿死是一个**真 bug，但只是「挂的方式之一」**。
静默硬死那种还没有解释。剩下唯一嫌疑是「PL/AXI 读不返回，两核一起卡在总线访问上」
（那样报 stall 的 kthread 自己都跑不上，所以串口全静）。

---

## 三、板上探针读数

### 3.1 RT 限流与调度类（`round6/rt_probe.txt`，流水线没跑时采的）

板上**只有 5 个** FF/RR 线程：

```
  PID   TID CLS RTPRIO PSR %CPU COMMAND
   11    11  FF     99   0  migration/0
   14    14  FF     99   1  migration/1
   75    75  FF     99   1  watchdogd
   90    90  FF     50   0  irq/29-mmc0
  529   535  RR     99   0  rtkit-daemon
```

`ksoftirqd/0`(PID 9)、`ksoftirqd/1`(15)、`rcu_preempt`(10)、CPU0 的 4 个 `kworker`
（含 `kworker/0:0H-mmc_complete`、`kworker/0:1H-mmc_complete`、`kworker/0:2H-mmc_complete`）
**全部 `TS`**（普通类）。

相关编译选项：`CONFIG_IRQ_FORCED_THREADING=y` / `CONFIG_NO_HZ=y` / `CONFIG_PREEMPT=y` /
`CONFIG_PREEMPT_RCU=y` / `CONFIG_RT_GROUP_SCHED=y` / `CONFIG_HZ=100`。
`/proc/sched_debug` **不存在**（没编 `SCHED_DEBUG`），所以看不到每核 `rt_rq` 的账本。

### 3.2 还剩下哪些能抓栈的路（`round6/probe_config.txt`）

```
/sys/module/rcupdate/parameters/rcu_cpu_stall_timeout=60     ← 默认 60 秒
now=0
kernel.panic_print = 31        ← 写得进去,实测支持(位 0/1/2/8)
after=31
restored=0
CONFIG_RCU_CPU_STALL_TIMEOUT=60
CONFIG_ARM_UNWIND=y
```

- `/proc/1/stack`、`/proc/self/stack` **都不存在**（没编 `CONFIG_STACKTRACE`）
- 板上同样没编：`CONFIG_DETECT_HUNG_TASK`（连
  `/proc/sys/kernel/hung_task_timeout_secs` 都没有）、`SOFTLOCKUP_DETECTOR`、
  `MAGIC_SYSRQ`（所以串口 BREAK 也不行）、`ftrace`
- `rcu_cpu_stall_timeout` 只能在 `/sys/module/rcupdate/parameters/` 下写，
  `/proc/sys/kernel/` 里没有这个名字；kernel 自己会夹到 ≥3 秒
- **`panic_print=31` 是目前唯一还能拿到栈的路**，而且只改「panic 时打什么」，不改行为

### 3.3 SD 卡账本（`round5/sd_probe.txt`，**只读命令**）

这一份是为了回答「是不是每次直接断电把 SD 卡弄坏了」。读数：

```
name=asdfg  oemid=0x000c  manfid=0x000005  serial=0x000003e8  date=03/2026
Filesystem state:         clean
Errors behavior:          Continue
Block count:              3806464
Filesystem created:       Thu Nov 18 03:38:04 2021
Mount count:              63
Maximum mount count:      -1
Last checked:             Thu Nov 18 03:39:01 2021   ← 等于创建那天,从没 fsck 过
Check interval:           0 (<none>)
[    3.346740] EXT4-fs (mmcblk0p2): recovery complete
[   45.207511] FAT-fs (mmcblk0p1): Volume was not properly unmounted. ...
 179       0 mmcblk0 6288 3900 395813 22757 336 366 18784 48148 0 24090 53090
/dev/root  15G  7.8G  5.7G  58% /
```

- 内核 dmesg 里**一条 mmc / ext4 / I/O 错都没有**（`grep -iE "mmc|sdhci|ext4|jbd2|i/o error"` 只回了开机那几行）
- `Filesystem state: clean` —— ext4 自己认为它没坏
- `recovery complete` 是**日志回放成功**，不是失败；脏关机留下的是「要回放」而不是「坏块」
- `Errors behavior: Continue` —— 真出 I/O 错也不会 remount-rw，只会记一笔
- **卡名 `asdfg`、`Mount count: 63` 且从没 fsck 过**，是旁证不是证据
- 写入很慢：开机 51.76 秒时 `wr_sect=18784`（9.6 MB）、`wr_ms=48148`（48.1 秒）
  ⇒ **约 200 KB/s**（正常是 MB/s 到几十 MB/s）。这解释了「SD 只是燃料」

**结论：没有证据说卡坏了**，而且因果顺序是反的 —— 板子先硬死，断电是被迫的。
但这一份是**编辑前的 `sd_probe.py`** 采的，缺三个从没读过的计数
（`/sys/kernel/debug/mmc0/err_stats`、`/sys/fs/ext4/mmcblk0p2/errors_count`、
`lifetime_write_kbytes`）。这三条已经加进 `sd_probe.py` 和 `next_round.py` 的
`pre_probe()`，**下次板子一上电就会自动记下来**。

### 3.4 死前的主机侧快照（`tools/_tmp/roundN/mon_*.log`）

每臂最后一份成功拉到的快照：

| 臂 | 最后一份快照 up | `cpu0` 行（usr nice sys idle iow irq softirq） | 死前 D 状态任务 | mmc0 中断 |
|---|---|---|---|---|
| `K_clean` | 688.74 | 4861 4 2378 60998 277 0 41 | 无 | 29: 31947 |
| `N_clean` | 59.83 | 2817 4 1170 1585 218 0 27 | 无 | 29: 21875 |
| `P_sdquiet` | 90.21 | 3230 0 1252 4064 273 0 33 | 无 | 29: 23618 |
| `Q_stack` | 74.02 | 1976 0 1213 3812 186 0 11 | 无 | 29: 21869 |
| `A_dump` | 314.38 | 5368 2 1257 24408 191 0 26 | **`jbd2/mmcblk0p2-` + 2×`journal-offline`** | 29: 23738 |
| `N_short` | 535.64 | 2561 2 1252 49277 245 0 35 | 无 | 29: 22658 |
| `R_rtoff` | 58.03 | 2739 3 1206 1487 186 0 21 | 无 | 29: 21856 |

- 只有 `A_dump` 那一轮死前 **D 状态里有 `jbd2` + 两个 `journal-offline`**（卡在不可中断等待）。
- 其余六臂**死前的快照都是健康的**：没有 D 状态任务、`procs_blocked 0`、
  `nr_writeback 0`、CPU0 的 `idle` 还在涨 ⇒ **不是慢慢坏掉，是被一刀切断**。
- 拉取器时间线（`.stamp`）能看到「最后一个成功是哪一秒」：

```
round7/mon_N_short_1.stamp  末次 ok [23:07:33] up=535.64   →  [23:09:07] 失败 TimeoutError
round7/mon_R_rtoff_1.stamp  末次 ok [23:09:58] up=58.03    →  [23:13:47] 失败 TimeoutError
round6/mon_A_dump_1.stamp   末次 ok [22:57:58] up=314.38   →  [22:58:33] 失败 TimeoutError
```

---

## 四、已经排除的（别回头再查）

| 嫌疑 | 怎么排掉的 |
|---|---|
| 电源 / 12V | 换 12V 照样复现（12V 仍要接，USB 带不动满载） |
| 换页 | `/proc/swaps` 已空、`vm.swappiness=0`，照样挂 |
| 硬件看门狗能自救 | **死代码**。实测武装 + 停喂 90 秒不复位；换 `setsid nohup` 起也不复位；一锤定音那次从武装到自然退出近 380 秒一次没喂也没重启 ⇒ 这板的 SWDT 压根不复位 |
| 我的黑匣子在添乱 | `K_clean`（板子上一个监控都不开）照样挂 |
| 拖滑杆 / 写 `fir_tune.json` | `run_live.sh` 根本不写 tune 文件，而 10-15 挂的那次跑的正是它。「每次死亡都紧跟拖滑杆」是混淆：拖滑杆固定在 +10 秒、死亡固定在 +16~21 秒 |
| PL 时序 / CDC | `fir_wrapper_timing_summary_routed.rpt`：WNS **+0.125 ns**、WHS **+0.015 ns**、TNS/THS 0、失败端点 0 |
| 音频 IP 写寄存器会被挂住 | `user_logic.vhd` 的 `IP2Bus_WrAck <= slv_write_ack`，而 `slv_write_ack` 是 `Bus2IP_WrCE(0..4)` 的或 ⇒ 纯地址译码，与 FIFO 满不满无关 |
| wav 循环回绕 | `board_input_48k.wav` 正好 288000 帧 = 6.000 秒 = 600 块，回绕点落在死亡窗口里；但 PASS 过的那轮 `n` 已经到 872 |
| ~~实时优先级~~ | **当年排错了。** 它就是 panic 的根因（上面第二节）。当年那次「关掉跑 480 s 不挂、开回来再跑 480 s 也不挂」是样本太少 —— 基准概率本来大约每 8 分钟一次 |

## 五、还没排除的

1. **静默硬死**（`R_rtoff` 那种：两核都停、串口全静、连 panic 都打不出来）。
   唯一说得通的解释是「PL/AXI 读不返回，两核一起卡在总线访问上」——
   那样 `panic_on_rcu_stall` 的 kthread 自己都跑不上。**没有任何证据，只是唯一不矛盾的猜想。**
2. **已知的地雷（这次的凶手不是它，但别踩）**：`board/overlay/fir.hwh` 里音频 IP
   声明的是 `0x43C00000`–`0x43C0FFFF`（64 KB），但 `user_logic.vhd` 的
   `IP2Bus_RdAck` 只是 `Bus2IP_RdCE(0..4)` 的或 —— **只有那 5 个寄存器命中才应答**。
   访问 `0x43C00014` 之后的任何地址 → AXI 读永不返回，而 **Zynq-7000 的 M_AXI_GP
   没有 AXI 超时**，CPU 就死在那句话上。写侧一模一样。
   现有代码只碰 `0x00`–`0x10`，所以没踩到。对比：HLS 的 FIR `s_axi_control`
   （`0x43C10000`–`0x43C1FFFF`）**全范围都应答**，安全。
3. **概率本身在随时间变**。同一条命令 10-15 挂过、也跑满过 480 秒两次；
   10-16 白天 `LIVE_SECONDS=25` 那轮 soak 四段全过；到深夜变成「几乎必挂」。
   板上 `fir_live.py` 那天只动了启动路径，所以是**机器的状态在变，不是代码在变**。

---

## 六、怎么复现

主机侧（Windows，工作目录 `C:\Users\XIA\Desktop\PYNQ音频项目`）：

```sh
# 串口抓现场(必须开着,不然 panic_print 打出来也没人接)
python tools/pynq_serial_capture.py COM6 21600 tools/_tmp/round8/console.log

# 另一头跑对照。NEXT_SETTLE 把每臂起跑 uptime 对齐 —— 这次的教训就在这
NEXT_SETTLE=180 NEXT_WAITMIN=180 NEXT_HANGWAIT=6 \
  python tools/_tmp/next_round.py tools/_tmp/round8 2 Z_idle N_long
```

板上（`one_run.sh` 是驱动，`next_round.py` 自己推上去）：

```sh
sh /home/xilinx/one_run.sh "LIVE_SRC=wav LIVE_SCOPE=1 DUMP=1 NOBOX=1" 8
```

配置表在 `next_round.py` 的 `CONFIGS`；`Z_idle` 是**什么都跑**的对照组
（`NOBOX=1 IDLE=1 IDLE_S=300 DUMP=1 NOSIG=1 SELFW=380 TMOUT=450`）。

判「断电」还是「死机」，看两个信号一起：

| | 电脑侧网口 | COM6 |
|---|---|---|
| **断电**（或用户关机） | **Disconnected** | **消失** |
| **死机** | **Connected / 1 Gbps**（PHY 不用 Zynq 配置就能自协商） | 设备还在，但**一个字都不吐** |

再加 `arp -a` 里连 192.168.2.99 的条目都没有 ⇒ **内核整个停了**（ARP 是内核答的）。
注意 192.168.2.1 在「以太网」这张真网卡上；「以太网 2」是 Sangfor SSL VPN 的虚拟网卡，
永远 Disconnected，**别看错那一行**。
