---
name: vivado-windows-batch-build
description: >
  Why a Vivado 2020.2 batch build on Windows intermittently dies with
  "couldn't read file <X>: no such file or directory" for a file that plainly
  exists, why the file it complains about is different every run, and why
  "just retry" makes it worse. The root cause covered here is TWO build loops
  running at once against the same project directory: create_project -force
  deletes and recreates that directory, so one instance reads while the other
  writes, and a per-round "taskkill vivado.exe" makes the two loops kill each
  other's work. Also covers the sibling bug where the script does
  "cd $build_dir" before "create_project -force $build_dir", which deletes the
  process CWD and produces the distinctive ": No error" variant, and the
  wrong success test for extracting .hwh from .hwdef.
  Use when a Vivado build fails with a file-not-found error for an existing
  file, when the failing file name changes between attempts, when errors
  escalate to "Failed to load feature 'ipintegrator'", or when a retry script
  "succeeds" but the bitstream timestamp never moves.
license: MIT
compatibility: Vivado 2020.2 on Windows 11 + Git Bash; xc7z020clg400-1
metadata:
  version: "1.0.0"
  updated: "2026-10-28"
---

# Vivado 报"文件不存在"，而文件就在那儿

2026-10-28 实测。表现是**每次挂在不同文件上**：

```
couldn't read file "mig_utils.tcl": no such file or directory
couldn't read file "utils.tcl": no such file or directory
couldn't read file "init.tcl": No error
ERROR: [Common 17-217] Failed to load feature 'ipintegrator'
```

那些文件全都在安装目录里、都能正常读（`mig_utils.tcl` 4410 B、
`xroe_framer_ip_v4.tcl` 12656 B，逐个核过）。安装目录最后修改时间还是装的那天，
今天零写入。所以**不是文件损坏、不是杀软、不是磁盘**。

## 什么时候用

- 批处理构建随机失败，报 `couldn't read file ...`
- 每次失败点到的文件不一样
- 加了"失败自动重试"之后成功率反而下降
- 重试脚本报 `OK`，但产物时间戳没变

## 四个真实原因（按发生率排）

### 1. 两个构建循环同时在跑（最容易中，也最像"环境玄学"）

`build/run_xxx.sh` 被同时启动两次（后台任务叠加、或上一次没退干净就再起一次），于是：

- 两个循环各起一个 `vivado.exe`；
- 两个都往**同一个** `build/vivado/<name>` 做 `create_project -force`
  —— 那一步会**把目录删掉重建**；
- 一个在写、一个在读 ⇒ 报"文件不存在"，而文件其实一直在；
- 脚本每轮开头若有 `taskkill /F /IM vivado.exe`，两个循环会**互相杀掉**
  对方正在写的 Vivado ⇒ 每次点到的文件都不一样，最后升级成
  `Failed to load feature 'ipintegrator'`。

判据（一眼定案）：

```
Get-CimInstance Win32_Process -Filter "Name='bash.exe'" |
  Where-Object { $_.CommandLine -like '*run_xxx_build*' } |
  Select-Object ProcessId, CreationDate
Get-Process vivado | Select-Object Id, StartTime, @{n='MB';e={[int]($_.WorkingSet64/1MB)}}
```

出现**两个同脚本进程**、或**两个 vivado 的 StartTime 相差几秒**，就是它。

> 注意：Vivado 自己也会拉子进程（`cmd /c vivado.bat -log xxx_wrapper.vds
> -source xxx_wrapper.tcl`，IP 的 out-of-context 综合）。那不是并发的第二个构建，
> 看父进程命令行就能区分。

修法：锁必须**带 PID**，只有锁的主人能删，僵死锁可接管。

```bash
LOCK=tools/board_debug/_tmp/build.lock
if [ -f "$LOCK" ]; then
  old=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
    echo "另一个构建循环还在跑（PID $old），退出"; exit 1
  fi
  echo "接管僵死锁"
fi
echo $$ > "$LOCK"
trap '[ "$(cat "$LOCK" 2>/dev/null)" = "$$" ] && rm -f "$LOCK"' EXIT
```

原来写的是 `mkdir` 上锁 + `rmdir` 解锁，**退出时无条件删**：任何旧循环退出都会把
新循环的锁删掉，于是第三个又能进来。

### 2. `cd $build_dir` 之后 `create_project -force $build_dir`

等于把**当前目录**删掉重建。Windows 下进程的 CWD 变成已失效目录，之后 Vivado 内部
任何**相对路径** `source` 都会失败，报错里带一句 `: No error`
（系统调用其实返回了 `ERROR_SUCCESS`，Tcl 就把 errno 0 印成 "No error"）。

**看到 `: No error` 就想到这条。**

修法：停在父目录。

```tcl
file mkdir $build_dir
cd [file dirname $build_dir]        ;# 不是 $build_dir
create_project -force audio_pl $build_dir -part $part_name
```

### 3. 拆 `.hwh` 的成功判据看错了 exit code

`.hwdef` 是个 zip，要从里面抠 `.hwh`。Vivado 的 `exec` 环境里 python 经常
**返回非 0 却已经把文件写好**，于是：

```tcl
# 错：catch 到非 0 就认为失败
if {![catch { exec $py -c {...} $hwdef $hwh }]} { set ok 1 }
```

一轮明明位流都写出来了，却因为这一步被判"失败"，重试又把好位流覆盖掉。

```tcl
# 对：只看文件写出来了没有
catch { exec $py -c {...} $hwdef $hwh }
if {[file exists $hwh] && [file size $hwh] > 0} { set ok 1 }
```

同理，**"成功"也不能 grep 脚本自己回显的标记**：`build_audio_pl.tcl` 里的
`puts "### DONE"` 会连同被回显的 tcl 源码一起出现在日志里，`grep '### DONE'`
恒为真。判据要加"产物真的被这一轮重写过"（`[ $BIT -nt $MARK ]`）。

### 4. FlexNet license 加载随机失败（`create_bd_design` 报随机文件不存在）

**症状**：`create_bd_design` 挂，报

```
couldn't read file "E:/Xilinx/Vivado/2020.2/data/rsb/busdef/interrupt_signal/busdef.tcl":
    no such file or directory
ERROR: [BD 41-69] Error sourcing TCL script
    (in namespace eval "::xilinx.com_signal_interrupt_1.0" script line 1)
```

**每轮点到的文件都不一样**（`AXIMM/busdef.tcl`、`testbench.tcl`、
`interrupt_signal/busdef.tcl`、`mig_utils.tcl`，还有 `[Ip 78-90] Error in
initialization of Rule object`），而**这些文件一个个都在、都能读**。
最恶劣的一种变体干脆只有一行，还在 **stderr** 里：

```
ERROR: [Common 17-217] Failed to load feature 'ipintegrator'.
```

**真因：FlexNet license/feature 加载随机失败。** 同一个 feature 名（core /
ipservices / ipintegrator）随机失败的案例在 AMD 官方论坛和社区都有记录，
结论一致：**重跑就好，纯随机**。2026-10-28 在这上面烧了整整一天（先冤枉并发、
再冤枉 build 目录残留、一度怀疑到内核过滤驱动），全部作废。

**为什么查了一天**：三条路全被堵——
1. `grep -m1 '^ERROR'` 只看 stdout，17-217 在 **stderr**，被吞（这是最大的坑；
   构建壳后来改成 `> log 2>&1` 才看见）；
2. 随机性让人永远能找到"这次和上次不一样"的环境差异（目录、CWD、TMP……），
   每一个都能自圆其说；
3. 成功过的轮次（比如手动复测的那几次）让人觉得"修好了"，失败又推翻。

**修法**：不要修，**重试吸收**。`build/run_pl_build.sh` 的重试壳改对成功判据后，
round 1 挂 license、round 2 一路过到出位流（16:14），全程无人值守。
两个配套动作：
- 日志必须 **stderr 合流**（`> "$LOG" 2>&1`），失败信息打 `grep -E '^(ERROR|CRITICAL)'`
  全部命中行，别只打第一条；
- 每轮换全新工程目录（`FIR_PL_BUILD_DIR`），`taskkill` 后的 Windows 句柄残留
  会让复用目录不可靠 —— 这不是根因，但让重试更稳。

**别做的事**：不要重装 Vivado、不要关杀软、不要查内核过滤驱动、不要怀疑 E 盘、
**更不要试图"定位随机性"**——它是 license 管理器的固有毛病，重试是唯一正解。

### 5. `CRITICAL WARNING` 不阻断构建（地址忘分配，位流白编）

给 BD 挂了新从机 `fir_pl_regs_0`，`connect_bd_intf_net` 接好、validate 过了，
但忘了 `assign_bd_address`。validate 只给：

```
CRITICAL WARNING: [BD 41-1356] Slave segment </fir_pl_regs_0/s_axi/reg0>
    is not assigned into address space </ps7_0/Data>
```

它是 **WARNING 不是 ERROR**——BD 照样保存、HDL 照样生成、位流照样编出来、
"成功判据"照样打绿灯。上板后 PS 读这个地址直接总线错，从位流外表完全看不出来。
2026-10-28 这条告警在日志里躺了三轮没被看见。

**修法两件套**（`build_audio_pl.tcl` 已加）：
1. `assign_bd_address` 一条不少；
2. 分配完**主动数一遍** `get_bd_addr_segs`，五个从机缺任何一个就 `error` ——
   把"WARNING 级别的接线遗漏"升级成"构建失败"。

**教训**：`validate` 通过 ≠ 接线正确。每次改 BD，都要对着日志把 CRITICAL WARNING
清零，或者像上面那样把关键不变量写成断言。

## 怎么证明"不是文件系统/杀软/磁盘"

在 **Vivado 自己的 Tcl 里**压测同一批文件，绕开构建流程：

```tcl
set d [file join $::env(XILINX_VIVADO) scripts ipintegrator]
set fails 0
foreach f {utils.tcl mig_utils.tcl board_utils.tcl sc_cosim_util.tcl gt_utils.tcl} {
  for {set i 0} {$i < 300} {incr i} {
    if {[catch {set ch [open [file join $d $f] r]; read $ch 200; close $ch}]} { incr fails }
  }
}
puts "STRESS fails=$fails"
exit
```

实测 3600 次打开 **0 失败**、12 次 `source` **0 失败** —— 文件 I/O 没问题，
问题在"谁在同时改这个目录"。

## 验收

- 同一时刻 `bash` 侧只有**一个**构建脚本进程，`vivado.exe` 只有一个主进程
- 日志里失败轮次归零（偶发单轮失败仍属正常，重试壳应该吸收掉）
- 产物 md5 每轮**不同**（说明真的重编了，而不是复用旧文件）

## 相关

- `pynq-codec-i2c-access`：另一次"读数全是常数"的误判，原因是 PL 没配置
- `pynq-audio-playback`：板子不出声的七个坑
