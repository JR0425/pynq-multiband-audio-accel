# PYNQ-Z2 直连 Windows 笔记本

网线直连，中间没有路由器。目标：浏览器打开板子的 Jupyter。
实测：PYNQ-Z2 + PYNQ 2.7.0 镜像 + Windows 11，2026-09-25。
其他「Linux 开发板 + 网线直连 PC」的场景也用得上。

## 最终配置

- 板子 eth0：静态 `192.168.2.99`，掩码 `255.255.255.0`
- 笔记本网卡：静态 `192.168.2.1`，掩码 `255.255.255.0`，网关和 DNS 留空
- 打开 `http://192.168.2.99:9090`，账号密码都是 `xilinx`

PYNQ-Z2 没有 Wi-Fi，只有有线网口。

## 五个坑

### 1. 笔记本网卡是 `169.254.x.x`

`169.254.x.x` 是 Windows 拿不到 DHCP 时的兜底地址，不是网卡坏了。
板子地址是写死的、不发号，所以两边不在一个网段。

处理：给笔记本网卡设静态 `192.168.2.1`。

### 2. 在 IPv4 属性里点了「确定」，但没生效

现象：IP 没上去。有时被说成「DNS 改不回自动」，`ipconfig` 复查还是 `169.254`、
`DHCP 已启用: 是`。是整个对话框都没提交，不是某一项的问题。

原因：点「确定」时背后弹了 UAC 提权框，被漏点了。界面不报错，改动静默丢弃。

处理：改用命令行，管理员身份执行：

```
netsh interface ipv4 set address 7 static 192.168.2.1 255.255.255.0
netsh interface ipv4 set dnsservers 7 source=dhcp
```

`7` 是网卡的 `ifIndex`。要用 ifIndex，别用中文网卡名，会踩 UTF-8 / GBK 编码坑。
ifIndex 用 `Get-NetAdapter` 查。

### 3. 电脑是 192.168.2.1，但网段里没设备应答

ping 板子超时，扫 1~254 只有自己，ARP 表里目标地址是 `Incomplete`。
板子其实收得到包，只是 eth0 上没有 IPv4 地址，回不了话。
别在主机端继续试，串口进板子看（见坑 4）。

### 4. 板子活着但网上找不到：走串口

- 认口：板子调试口是 FTDI USB Serial。`COM3` / `COM4` 常是蓝牙虚拟口，别认错。
- 连接：115200 8N1，`DtrEnable` 和 `RtsEnable` 都要 true。
- PYNQ 镜像自带自动登录，发一个回车就出 `xilinx@pynq:~$`，不用输账号密码。
- sudo 密码是 `xilinx`：`echo xilinx | sudo -S <命令>`

比在主机端扫网段、猜 IP 快得多。

### 5. 真凶：dhclient 反复重配 eth0，把静态地址冲掉

现象：`ifconfig eth0` 只有 IPv6 link-local、没有 IPv4，但 `RX packets` 在涨。
原因：出厂配置是 `eth0 = dhcp` + `eth0:1 = static`，
dhclient 无限重试 DHCP，每次重配都把静态别名冲掉。
`journalctl -b | grep -i eth0` 能看到它一直在等一个不存在的 DHCP 服务器。

处理：停掉 dhclient，把 `/etc/network/interfaces.d/eth0` 改成纯静态：

```
auto eth0
iface eth0 inet static
address 192.168.2.99
netmask 255.255.255.0
```

代价：板子插路由器不会自动拿 IP 了。

## 验收

能 ping 通不算数，那可能是上一轮手工设出来的残留。重新上电再验一遍。

| 查什么 | 哪里 | 命令 | 通过标准 |
|---|---|---|---|
| 外部可达 | 笔记本 | `ping 192.168.2.99` | 0% 丢失 |
| 服务在线 | 笔记本 | `curl -o /dev/null -w "%{http_code}" http://192.168.2.99:9090/` | `302` |
| 确属冷启动 | 板子 | `uptime` | 数值很小，如 `up 2 min` |
| 地址自动恢复 | 板子 | `ifconfig eth0` | 有 `192.168.2.99` |
| 无残留进程 | 板子 | `pgrep -a dhclient` | 输出为空 |
| 配置未被改 | 板子 | `cat /etc/network/interfaces.d/eth0` | 4 行，与写入一致 |

`uptime` 那条最省事：数字小就说明是刚开机，配置是开机时自己读的，不是手工设的。

## 改配置的一条铁律

改完配置文件，下一条必须 `cat` 读回来。

实测踩过：本意是把密码喂给 sudo

```
printf '...' | echo xilinx | sudo -S tee /etc/network/interfaces.d/eth0
```

`echo` 不转发 stdin，把 `xilinx` 这七个字母直接写进了配置文件，
覆盖掉了一份好的网络配置。靠下一步 `cat` 回读才发现：预期 4 行，实际只有 `xilinx` 一行。

正确写法是把密码和内容分开，不抢同一个 stdin：

```
echo xilinx | sudo -S bash -c 'echo "auto eth0" > /tmp/x ; echo "..." >> /tmp/x'
```

管道、重定向、sudo 密码三件事同时出现时出错率很高，不能假设它写对了。

## 日常自检

双击 `skill/checkers/check_board_connection.bat`，会打印笔记本网卡地址、ping 板子的结果、打不开时怎么办。
