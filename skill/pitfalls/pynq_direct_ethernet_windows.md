# PYNQ-Z2 直连 Windows 笔记本

网线直连，中间没有路由器。目标：浏览器打开板子的 Jupyter。
实测：PYNQ-Z2 + PYNQ 2.7.0 + Windows 11，2026-09-25。

## 最终配置

- 板子 eth0：静态 `192.168.2.99` / `255.255.255.0`
- 笔记本网卡：静态 `192.168.2.1` / `255.255.255.0`，网关和 DNS 留空
- `http://192.168.2.99:9090`，账号密码都是 `xilinx`

## 五个坑

**1. 笔记本网卡是 `169.254.x.x`**
Windows 拿不到 DHCP 的兜底地址，不是网卡坏了。板子地址写死、不发号，两边不同网段。
→ 给笔记本网卡设静态 `192.168.2.1`。

**2. 点了「确定」但没生效**
`ipconfig` 还是 `169.254`、`DHCP 已启用: 是`。是整个对话框没提交，界面不报错。
原因：点确定时背后弹了 UAC 提权框，被漏点了。
→ 管理员身份执行：

```
netsh interface ipv4 set address 7 static 192.168.2.1 255.255.255.0
netsh interface ipv4 set dnsservers 7 source=dhcp
```

`7` 是网卡 ifIndex（`Get-NetAdapter` 查）。别用中文网卡名，会踩编码坑。

**3. ping 不通，扫 1~254 只有自己**
ARP 表里目标地址 `Incomplete`。板子收得到包，但 eth0 上没有 IPv4 地址，回不了话。
→ 别在主机端继续试，走串口。

**4. 走串口进板子**
认口、参数、怎么读：`skill/pitfalls/pynq_serial_console.md`。
sudo 密码是 `xilinx`：`echo xilinx | sudo -S <命令>`

**5. 真凶：dhclient 把静态地址冲掉**
`ifconfig eth0` 只有 IPv6、没有 IPv4，但 `RX packets` 在涨。
出厂配置是 `eth0 = dhcp` + `eth0:1 = static`，dhclient 无限重试 DHCP，每次重配都把静态别名冲掉。
`journalctl -b | grep -i eth0` 能看到它一直在等一个不存在的 DHCP 服务器。
→ 停掉 dhclient，`/etc/network/interfaces.d/eth0` 改成：

```
auto eth0
iface eth0 inet static
address 192.168.2.99
netmask 255.255.255.0
```

代价：板子插路由器不会自动拿 IP 了。

## 验收

能 ping 通不算数，可能是上一轮手工设出来的残留。**重新上电再验一遍。**

| 查什么 | 哪里 | 命令 | 通过标准 |
|---|---|---|---|
| 外部可达 | 笔记本 | `ping 192.168.2.99` | 0% 丢失 |
| 服务在线 | 笔记本 | `curl -o /dev/null -w "%{http_code}" http://192.168.2.99:9090/` | `302` |
| 确属冷启动 | 板子 | `uptime` | 数值很小，如 `up 2 min` |
| 地址自动恢复 | 板子 | `ifconfig eth0` | 有 `192.168.2.99` |
| 无残留进程 | 板子 | `pgrep -a dhclient` | 输出为空 |
| 配置未被改 | 板子 | `cat /etc/network/interfaces.d/eth0` | 与写入一致 |

`uptime` 那条最省事：数字小就说明刚开机，配置是开机时自己读的，不是手工设的。

## 改配置的铁律：`cat` 读回来

踩过：本意是把密码喂给 sudo，结果 `echo` 不转发 stdin，
把 `xilinx` 七个字母直接写进了配置文件，覆盖掉一份好的网络配置。

```
printf '...' | echo xilinx | sudo -S tee /etc/network/interfaces.d/eth0   # 错
```

靠下一步 `cat` 回读才发现：预期 4 行，实际只有 `xilinx` 一行。
密码和内容要分开，不抢同一个 stdin：

```
echo xilinx | sudo -S bash -c 'echo "auto eth0" > /tmp/x ; echo "..." >> /tmp/x'   # 对
```

## 日常自检

双击 `skill/checkers/check_board_connection.bat`，会打印笔记本网卡地址、ping 结果、打不开怎么办。
