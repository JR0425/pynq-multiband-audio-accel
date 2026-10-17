# 板子死机 2026-10-17 现场账本

本文件记录 10-17 这天的死机实验读数。配套四份串口转储（原始文本，未改动）：

- `board_hang_rcu_stall_dump_20261017.txt` —— round8 `N_long` 第 1 轮
- `board_hang_rcu_stall_dump_20261017_n2.txt` —— round8 `N_long` 第 2 轮
- `board_hang_rcu_stall_dump_20261017_r9.txt` —— round9 `N_short` 第 1 轮
- `board_hang_rcu_stall_dump_20261017_600s.txt` —— round12b `N_long600` 第 1 轮

**一句话现状**：10-16 的「把实时关掉照样挂」在定住起跑 uptime 后**没有复现**。
实时开着时，真正的问题是搬样线程把 CPU0 的 `ksoftirqd` 和 `kworker` 饿死。
只提 `ksoftirqd` 能撑过 300 秒，但 600 秒会挂 —— 转储点名 `kworker/0:1`（P21）。
把 `kworker/[01]:*` 一起提到 FIFO 50 后，**600 秒全活**（round13，拉取器 212/212 应声，
丢块 539、0.991x）。开机服务另加 30 秒重扫 timer，覆盖运行时新建的 kworker。

---

## 一、逐轮记账

原始主机侧日志在本地 `tools/_tmp/roundN/round_host.log`（未入库）。
所有臂都用 `NEXT_SETTLE=180`，起跑 uptime 对齐。

### round8：机器 vs 管线

| 配置 | 起跑 up | 最后应声 | 结果 | 备注 |
|---|---|---|---|---|
| `Z_idle` #1 | 189 | 495 | **PASS** | 什么都不跑，300 秒 |
| `N_long` #1 | 498 | 506（21:59:45 最后一次，22:00:01 起失联） | **HANG** | 管线 300 秒 |
| `Z_idle` #2 | 187 | 493 | **PASS** | |
| `N_long` #2 | 496 | 529.66（22:10:08 最后一次，22:10:21 起失联） | **HANG** | |

判读：同样的 300 秒，什么都不跑 2/2 活；跑管线 2/2 挂。
**「机器自己在坏」这条排掉了，触发点在管线这一侧。**

### round9：定住起跑条件后，实时开关的 A/B

| 配置 | 起跑 up | 结果 | 备注 |
|---|---|---|---|
| `N_short` #1（实时开） | 183 | **HANG** | 14~31 秒内失联；转储见 `_r9.txt` |
| `R_rtoff` #1（实时关） | 186 | **PASS** | |
| `N_short` #2（实时开） | 296 | **PASS（假的）** | 第 37 秒起网络失联 64 秒；程序在 90 秒先结束，没等到 stall 计时器 |
| `R_rtoff` #2（实时关） | 406 | **PASS** | |

`N_short` #2 这一臂说明**判据不能只看 `RESULT=done`**：
拉取器的 `.stamp` 里 22:22:17~22:23:11 连续超时，期间内核路径已经不通了。
90 秒的臂会把它记成 PASS，因为程序先退出、把 CPU0 还给了内核。

### round10：300 秒复核实时开关

| 配置 | 起跑 up | 结果 | 丢块 |
|---|---|---|---|
| `R_long` #1（实时关） | 551 | **PASS** | 552 / 300 秒（1.84 块/秒） |
| `N_long` #1（实时开） | 872 | **HANG** | — |
| `R_long` #2（实时关） | 186 | **PASS** | 567 / 300 秒（1.89 块/秒） |
| `N_long` #2（实时开） | 506 | **HANG** | — |

关实时确实能活，但每 0.54 秒丢一块（每块 10 ms），耳机里是持续断续，不能拿去演示。
实时开着又必挂。两条路都不通，得换第三种。

### 修复验证：实时全开 + `ksoftirqd` 提权

| 条件 | 起跑 up | 结果 | 丢块 | 实时倍率 |
|---|---|---|---|---|
| 120 秒，`ksoftirqd/0`=FIFO 50（手工） | 203 | **PASS** | 57 | 0.995 |
| 300 秒，`/0`+`/1`=FIFO 50（手工） | 358 | **PASS** | 61（0.20 块/秒） | 0.998 |

### round11：开机服务 + 正式驱动脚本，300 秒 ×2

| 配置 | 起跑 up | 结束 up | 结果 | 丢块 | 实时倍率 | 拉取器 |
|---|---|---|---|---|---|---|
| `N_long` #1 | 188 | 507 | **PASS** | 220（0.73 块/秒） | 0.9927 | 99 ok / 1 次超时 |
| `N_long` #2 | 509 | 827 | **PASS** | 226（0.75 块/秒） | 0.9925 | 106 ok / 0 超时 |

两臂都由 `ksoftirqd-rt.service` 开机自动提权，中间没有人工干预。
`N_long` #1 的那 1 次超时是 23:00:47→23:01:08 的单次 SSH 连接超时，
隔一个周期就恢复，不是 stall 那种连续 60 秒失联。

丢块比手工探针（61）高约 3.7 倍，差别很可能在于正式轮次同时开着 SSH 拉取器
（每 3.5 秒连一次板子），拉取器本身的网络/SSH 负载又给 CPU0 增加了软中断。
演示时状态文件在板上本地读，不走 SSH，所以实际应更接近手工值。
即便如此，220/226 也远低于关实时路线的 552~567。

### round12b：600 秒复测（只提 `ksoftirqd`）—— **HANG**

| 配置 | 起跑 up | 最后应声 | 结果 | 备注 |
|---|---|---|---|---|
| `N_long600` | 947（23:13:19） | 1234.64（23:18:06，23:18:19 起连续失联） | **HANG** | 跑到约 390 秒挂 |

开机服务已在跑（启动日志有 `Raise ksoftirqd … SCHED_FIFO`），
所以 `ksoftirqd/0`、`/1` 都是 FIFO 50。这次挂的转储
（`board_hang_rcu_stall_dump_20261017_600s.txt`）换了形状：

```
rcu: INFO: rcu_preempt detected stalls on CPUs/tasks:
rcu: 	Tasks blocked on level-0 rcu_node (CPUs 0-1): P21
Kernel panic - not syncing: RCU Stall
...
ksoftirqd/0     S    0     9      2 0x00000000
ksoftirqd/1     S    0    15      2 0x00000000
kworker/0:1     R  running task        0    21      2 0x00000000
```

`ksoftirqd` 这次是 `S`，提权生效了；但 `kworker/0:1`（pid 21 = P21）仍是 `R`，
RCU 直接点名它阻塞了宽限期。**只提 `ksoftirqd` 救不了 kworker。**

### round13：600 秒复测（`ksoftirqd` + `kworker` 都提权）—— **PASS**

| 配置 | 起跑 up | 结束 up | 结果 | 块数 | 丢块 | 实时倍率 | 拉取器 |
|---|---|---|---|---|---|---|---|
| `N_long600` | 209（23:22:52） | 821（23:33:10） | **PASS** | 59478 | 539（0.90 块/秒） | 0.991 | 212/212 全 ok，0 超时 |

`self_exit alive=n`（进程按 `LIVE_SECONDS` 自己跑完退出），最终状态 `ok=true`。
600 秒里拉取器每 3.5 秒一次、**212 次全部应声，没有一次超时** —— 不是假 PASS。
丢块 539 对比 round11 的 300 秒 220（0.73 块/秒），速率略高但同一量级。

### round14：600 秒第二次确认（加了 30 秒重扫 timer）

开机服务现在除了启动时扫一次，还有 `ksoftirqd-rt-refresh.timer` 每 30 秒重扫
（workqueue 会按需新建 kworker，安装时板上已有 pid 2365、6531 等后来才出现的）。
复测结果填在这里（进行中）。

---

## 二、机制

三份转储同型。以 `_r9.txt` 为例：

- `rcu_preempt self-detected stall on CPU 0`，随后 `Kernel panic - not syncing: RCU Stall`。
- CPU0 上 `python3`（搬样线程，pid 1137 / 父进程 1095）是 `R running task`；
  同一时刻 `ksoftirqd/0`、`kworker/0:1`、`kworker/0:3H` 也都是 `R`
  —— **可运行但排不上 CPU0**。
- 搬样线程是 `SCHED_FIFO 20`，`duplex_block()` 里是纯 MMIO 轮询，按设计永不阻塞。
  `ksoftirqd` 是普通 CFS 类，在同一个核上永远让不过它。

链条：搬样线程占满 CPU0 → `ksoftirqd` 排不上 → CPU0 的 RCU 静默态报不上去 →
60 秒后判 stall → `panic_on_rcu_stall=1` → panic。

`ksoftirqd` 提权之后这条链还有后半截（round12b 抓到的）：`ksoftirqd` 能跑了，
但 **`kworker/0:1` 仍是 CFS**，同样永远排不上。它如果恰好在一次 RCU 读端临界区里
被抢占，就永远没法报静默态 —— RCU 转储直接把它点名为 `P21`
（`Tasks blocked on level-0 rcu_node ... : P21`，21 是 pid）。
所以修复必须同时覆盖 workqueue 的 kworker，不能只修 `ksoftirqd`。

「关实时能活」也是这条链的反面证据：CFS 下 `/proc/uptime` 的 idle 时间在走、
调度器会切走搬样线程，所以静默态报得上去；代价是每次被切走就丢一块。

**为什么 90 秒的臂会误判**：stall 要连续 60 秒才触发 panic。如果饿死发生在第 37 秒，
程序在第 90 秒退出、CPU0 还给内核，stall 计时器就被清零了 —— 板子不 panic，
但第 37~90 秒之间网络已经不通。所以以后看 `RESULT=done` 之外必须看
拉取器 `.stamp` 有没有连续超时。

---

## 三、修复

把 `ksoftirqd/0`、`ksoftirqd/1`、以及 `kworker/[01]:*` 提到 `SCHED_FIFO 50`
（高于搬样线程的 20 和过核线程的 10）。它们需要跑时能抢占搬样线程，
RCU 静默态和网卡软中断都能按时跑；不需要时搬样线程仍然独占 CPU，音质不掉。

不覆盖会怎样：

- 只提 `ksoftirqd`：300 秒能过（round11 两臂），600 秒在约 390 秒挂，转储点名 `kworker/0:1`（round12b）
- 只提 `ksoftirqd/0` 不提 `/1`：120 秒能过，更长的没验

实测 300 秒：丢块 61（0.20 块/秒），实时 0.998x。开 SSH 拉取器的正式轮次
（round11 两臂）也活着跑完，丢块 220/226（0.73~0.75 块/秒），实时 0.993x。对比：

| 配置 | 300 秒能否活 | 丢块/300 秒 |
|---|---|---|
| 实时开，不提权 | 挂 | — |
| 实时关 | 活 | 552~567 |
| **实时开，ksoftirqd 提权（手工）** | **活** | **61** |
| **实时开，ksoftirqd 提权（round11，带拉取器）** | **活** | **220~226** |

600 秒：只提 `ksoftirqd` 会挂（round12b）；同时提 `kworker/[01]:*` 后**活着跑完**
（round13：59478 块、丢 539、0.991x，拉取器 212/212 全应声）。

已做成开机服务，脚本在 `board/scripts/`：

```
ksoftirqd_rt.sh            # 找 pid、chrt -f -p 50；ksoftirqd/0、/1 和 kworker/[01]:* 都提
install_ksoftirqd_rt.sh    # 装成 /etc/systemd/system/ksoftirqd-rt.service
```

在板上装一次（要 root）：

```sh
sudo sh install_ksoftirqd_rt.sh
```

装完重启验证：

```sh
systemctl is-active ksoftirqd-rt.service      # active
for p in 0 1; do chrt -p $(pgrep -f "^ksoftirqd/$p$" | head -1); done   # FIFO 50
for pid in $(pgrep -f "^kworker/[01]:"); do chrt -p $pid; done           # 全部 FIFO 50
```

重刷 SD 卡之后要重新装一次。

---

## 四、复现命令

```sh
# 终端 A：串口录制（必须常开）
python tools/board_debug/pynq_serial_capture.py COM6 21600 tools/_tmp/roundN/console.log

# 终端 B：一臂一臂跑
NEXT_SETTLE=180 NEXT_WAITMIN=180 NEXT_HANGWAIT=6 \
  python tools/board_debug/next_round.py tools/_tmp/roundN 2 N_long
```

对照臂用 `Z_idle`（什么都不跑）。关实时用 `R_long` / `R_rtoff`
（`LIVE_RT_IO=0 LIVE_RT_DSP=0`）。

---

## 五、还没结的

- **round7 的 `R_rtoff` 静默硬死没有复现。** 那一臂起跑 uptime 只有 44 秒
  （开机风暴未过），此后 4 次定住起跑条件的关实时臂（round9 两次 90 秒、
  round10 两次 300 秒）都活着。暂时按「起跑条件没对齐的离群点」记，
  但不能断言第二种死法不存在。
- **只验证到 300 秒**这条已经补上：600 秒 round13 全活；round14 再做一次确认。
- 提权后 `ksoftirqd`、`kworker` 都是实时类，如果它们自己出现 I/O 风暴，
  会反过来挤搬样线程。600 秒里丢块 539（0.90 块/秒），比 300 秒的 0.73 略高，
  但仍在可用范围；更长时长要盯丢块曲线。
- **新 kworker 不会自动提权** —— 已由 `ksoftirqd-rt-refresh.timer` 每 30 秒重扫覆盖。
  安装时板上已存在 pid 2365、6531 等开机后才创建的 worker。
- round13 的 `self_exit after 475s` 里的 475 不是秒数：每圈 `sudo kill -0` 自身要花
  约 0.2 秒，475 圈实际跨了约 590 秒。最终状态 `n=59478`（594.8 秒音频）才是时长依据。
