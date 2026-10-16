# `tools/board_debug/` —— 板子死机的排查脚手架

2026-10-15 / 10-16 查「实时通路跑起来会把板子弄死」这件事时写的脚本。
现在挂在这里，下次再遇到板子挂掉或要做 soak，直接拿。

原始读数（每一轮的日志、探针输出、两份完整 panic 转储）在
`data/results/board_hang_evidence_20261016.md`；
完整经过、以及当时哪些判断错了，在
`report/llm_collab_log/2026-10-16_board_hang_rcu_stall.md`；
可复用的结论在 `skill/pitfalls/pynq_board_hang_capture.md`。

## 先分清「在哪跑」

这个目录里的脚本分两类，**跑错地方等于白跑**：

| 在哪跑 | 怎么上去 | 说明 |
|---|---|---|
| **主机侧**（Windows，conda 的 `python`） | 直接 `python <脚本>` | 靠 paramiko 走 SSH 连 `192.168.2.99`；连接信息（`xilinx`/`xilinx`）写在脚本顶部 |
| **板上**（`/home/xilinx/`） | 由 `next_round.py` 的 `push_file()` 自动推，或手工用 `put.py` | 需要 `sh`；**不是** python |

板上脚本一律先推上去再执行，不要就地编辑板上的副本 —— 板子一重启就没了。

## 文件

### 板上跑

| 文件 | 干什么 | 状态 |
|---|---|---|
| `hangmon4.sh` | 黑匣子：每秒往 `/dev/shm/hangmon.log` 抄一份内核计数 + `fir_live.py` 的状态文件 | **当前版** |
| `one_run.sh` | 走一遍 notebook 那条路：起实时 → 等稳 → 拖一次滑杆 → 发 SIGINT。行为由 `$1` 里的一串开关控制（`IDLE` / `DUMP` / `FASTSTALL` / `NOJRNL` / `WD` / `IRQ1` / `NOSIG` / `NOTUNE` / `STOP9` / `NOBOX`）。跑完打一行 `RESULT=done` | 当前版 |
| `hangmon3.sh` | 早期黑匣子 | **已废弃**，会写 SD、可能就是它自己把板子弄死的。留作反面教材 |
| `wd_guard.sh` | 只在 `fir_live.py` 活着时武装硬件看门狗 | **已判死**，这块板的 SWDT 实测不复位。留作记录 |

### 主机侧跑

| 文件 | 干什么 | 状态 |
|---|---|---|
| `pynq_serial_capture.py` | **抓串口**。能在板子被断电、串口设备消失又重新枚举之后自己接回来 —— 抓死机现场必须开着它 | 当前版 |
| `next_round.py` | **轮次驱动**。按 `CONFIGS` 里的配置串，一臂一臂地跑并记账；黑匣子与拉取器由它管 | 当前版 |
| `sd_probe.py` | 板子一回来先读它自己的健康账本（内核报过的 mmc/ext4 错、`tune2fs` 账本、卡身份、XADC 电压、SDHCI 的 `err_stats`）。**全是只读命令** | 当前版 |
| `rt_probe.py` | 读 RT 限流设置、板上所有 FF/RR 线程、内核线程的调度类、相关编译选项。只读 | 当前版（开头那段"背景"里的假设已被推翻，见文件头注释） |
| `probe_config.py` | 问清板上还能用哪些抓栈手段。只读为主，唯一的写是 `kernel.panic_print=31` | 当前版 |
| `pynq_console_cmd.py` | 往串口发**一条**命令并把回显抓回来。命令一律写成单行 | 当前版 |
| `put.py` | 走 base64 把一个小文件推到板上（SFTP 那条路不通）。`--exec` 加可执行位 | 当前版 |
| `soak_notebook.py` | 反复走 notebook 那条路并数"多少次里挂一次" | **已被 `next_round.py` 取代**，留作参考 |

两个串口脚本（`pynq_serial_capture.py` / `pynq_console_cmd.py`）**抢同一个 COM 句柄**，
同一时刻只能开一个。PowerShell 版的同类工具在 `skill/checkers/pynq_serial_*.ps1`，
编码的坑见 `skill/pitfalls/pynq_serial_console.md`。

## 最短的一次用法

```sh
# 终端 A:串口录制必须开着,不然 panic_print 打出来也没人接
python tools/pynq_serial_capture.py COM6 21600 tools/_tmp/roundN/console.log

# 终端 B:一臂一臂地跑。NEXT_SETTLE 把每臂的起跑 uptime 对齐
NEXT_SETTLE=180 NEXT_WAITMIN=180 NEXT_HANGWAIT=6 \
  python tools/board_debug/next_round.py tools/_tmp/roundN 2 Z_idle N_long
```

- `NEXT_SETTLE=NNN` 每臂起跑前等到板子开机满 NNN 秒
- `NEXT_WAITMIN=NNN` 板子挂了 / 关着时最多等几分钟（板子硬死只能人按电源，所以给大一点，按了电源就自动开跑）
- `NEXT_HANGWAIT=NNN` 挂了之后等它自己 panic 重启等几分钟

只想读一遍板子的账本、不跑任何东西：

```sh
python tools/board_debug/sd_probe.py tools/_tmp/roundN
```

## 用之前先看这几条规矩

都是这两天自己踩出来的：

1. **`NEXT_SETTLE` 一定要设。** 忘了设会让整轮作废 —— 板子刚开机时
   systemd / journald / udev 还在猛写 SD，那样「关掉某项设置」和「当时 SD 正忙」
   混在一起，这一轮判不了。
2. **`DUMP=1` 常开。** 它只改 "panic 时打什么"，不改行为，所以做 A/B 的每一臂都可以带着。
3. **判据用「能撑多久」，不用「挂了几次」。** 所以配置表里有 `Z_idle`（什么都不跑的对照组）
   和 `N_long`（长时长基线）。
4. **挂了就等它自己回来，等不到就收工。** 别拿着一条死板子把后面的配置一个个"跑"成假 HANG。
5. **别在 soak 跑的时候往板子上打别的重命令**（`journalctl -k -b -1` 这种猛读 SD 卡的会污染那一轮）。
6. **两个串口程序不能同时开**：`pynq_serial_capture.py` 和 `pynq_console_cmd.py`
   抢同一个 COM 句柄。要发命令就先停录制。
7. **板上判存活的 `kill -0` 要加 `sudo`** —— 板上跑的是 root 进程时，不加会因"没权限"
   返回非零，那条日志就永远报 `alive=n`。
