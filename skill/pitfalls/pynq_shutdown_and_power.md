---
name: pynq-shutdown-and-power
description: >
  Decide whether a PYNQ-Z2 can be unplugged or must be shut down in software
  first, and how to tell that the halt has actually finished. Use when the user asks
  how to power the board off, whether it is safe to just pull the cable, or worries
  about corrupting the SD card, and before packing the board away for the night.
license: MIT
compatibility: PYNQ-Z2 image 2.7.0
metadata:
  version: "1.0.2"
  updated: "2026-10-16"
---

# PYNQ-Z2 断电与关机

什么时候可以直接拔，什么时候必须先软件关机。
实测：PYNQ-Z2 + PYNQ 2.7.0 镜像，2026-09-25。

## 什么时候用

- 要收起来过夜、搬动板子之前
- 刚改过配置，或刚跑过会写盘的命令（pip / apt / 写文件 / 改网络）
- 不确定「现在拔掉会不会弄坏 SD 卡」

## 什么时候别用

- 只是想重启 Jupyter 或重跑一个脚本 —— 不用断电
- 板子正在录音、正在写文件 —— 先让它停干净，再走本文流程

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-25 | 初版 |
| 1.0.1 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |
| 1.0.2 | 2026-10-16 | 补「拔电到底把卡弄坏没有」的实测判据；指向新增的死机抓现场笔记 |

## 分三档

| 档 | 情况 | 怎么做 |
|---|---|---|
| 长期通电 | 白天在写代码、随时要用 | 别关。功耗很低，长时间通电是正常用法 |
| 直接拔 | 板子上没在跑东西、没写文件 | 直接断电，一般没事 |
| 必须软件关机 | 刚改过配置 / 要收起来过夜 / 刚跑完写盘操作 | 走下面流程，等它自己停干净再拔 |

判断标准不是「板子上有没有在跑东西」，而是「这一刻有没有人在写 SD 卡」。

## 为什么不能随手拔

Linux 有写缓存，文件看着写完了，实际可能还在内存里没落盘。
突然断电会丢内容，严重时文件系统损坏、卡起不来。

关键前提：手上没有可重烧的镜像。
SD 卡里是烧好的 PYNQ 镜像，卡写坏了没有备胎，整个项目当场停工。

所以第三档存在的唯一理由是：卡坏掉的代价远大于关机的麻烦。

## 怎么关

串口进板子（FTDI USB Serial，115200 8N1，自带自动登录，发个回车就出 `xilinx@pynq:~$`），执行：

```
echo xilinx | sudo -S poweroff
```

`sudo` 密码是 `xilinx`。`echo ... | sudo -S` 是把密码从管道喂给 sudo，避免交互输入。

然后看串口输出，等它自己走完：

```
Finished Power-Off.
Reached target Power-Off.
```

看到这两行才算停干净，这时才断电源。没看到就断电，正是最危险的时候。

## 关机前判断

1. 刚改过配置文件 → 软件关机
2. 刚跑过会写盘的命令（`pip install` / `apt` / 写文件 / 改网络配置）→ 软件关机
3. 要收起来过夜 → 软件关机
4. 以上都不是，就是想重插一下 → 直接拔

## 板子硬死的时候人是被迫断电的

板子挂到没有输出、连 panic 都没有的时候，唯一能做的就是按电源 —— 这不是「随手拔」，
是没得选。所以真正要回答的问题是：**这一次（和前面每一次）断电，把卡弄坏没有。**

这个不用猜，卡自己的账本可以读，全是只读命令（多数要 `sudo`）：

```sh
# ext4 自己认为它坏没有 + 挂载次数 + 上次 fsck
tune2fs -l /dev/mmcblk0p2 | grep -iE "state|mount count|check|error"
# 内核报过几次 mmc / ext4 / I/O 错
dmesg | grep -iE "mmc|sdhci|ext4|jbd2|i/o error"
# ext4 累计报过几次内部错 + 最近一次是什么函数哪一行
cat /sys/fs/ext4/mmcblk0p2/errors_count
# SDHCI 硬件层自己数的命令超时 / CRC 错
cat /sys/kernel/debug/mmc0/err_stats
# 卡的产品名 / 序列号 / 出厂日期
for f in name serial date; do printf '%s=' "$f"; cat /sys/block/mmcblk0/device/$f; done
# 这个文件系统有生以来被写了多少 KB（磨损）
cat /sys/fs/ext4/mmcblk0p2/lifetime_write_kbytes
```

2026-10-16 实测（一块被反复硬死、被迫断电多次的板子）：

| 读到的 | 值 | 怎么读 |
|---|---|---|
| `Filesystem state` | **clean** | ext4 自己认为它没坏 |
| `Errors behavior` | `Continue` | 真出 I/O 错也不会 remount-rw，只会记一笔 |
| dmesg 里的 mmc / ext4 / I/O 错 | **一条都没有** | 连一条坏块记录都没有 |
| 每次开机的 `EXT4-fs (mmcblk0p2): recovery complete` | — | **是日志回放成功，不是失败** |
| `Mount count` / `Last checked` | 63 次 / 停在镜像创建那天 | 从没 fsck 过；这是默认关掉了周期检查，不是故障 |
| 写入速度 | 约 200 KB/s | 慢，但它是「燃料」不是病因 |

**结论：反复硬死 + 被迫断电本身没有把卡弄坏。** 留下来的只有"要回放日志"，
不是坏块。（顺带：这块卡在内核里的产品名是 `asdfg` —— 旁证，不是证据。）

留一句反话：账本干净只说明**到目前为止**没坏，不说明断电无害。
第三档仍然存在，它保护的是那个「万一」，代价只是等一下。

## 相关

- 抓死机现场（栈、串口、判「断电」还是「死机」）：`skill/pitfalls/pynq_board_hang_capture.md`
- 网络配置：`skill/pitfalls/pynq_direct_ethernet_windows.md`
- 串口控制台：`skill/pitfalls/pynq_serial_console.md`
- 连通性自检：`skill/checkers/check_board_connection.bat`
