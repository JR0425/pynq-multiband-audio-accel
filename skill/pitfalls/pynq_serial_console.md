# PYNQ-Z2 串口控制台

不装 PuTTY，用 Windows 自带的 PowerShell 读板子的串口。
实测：PYNQ-Z2 + PYNQ 2.7.0，Windows 11，2026-09-26。

## 什么时候用

板子不知道 IP / Jupyter 打不开 / 想看开机报错。串口是不依赖网络的唯一通路。

## 端口号

设备管理器 → 端口。板子那条叫 `USB Serial Port (COMx)`，是板上的 FTDI 芯片。

叫「蓝牙链接上的标准串行」的两条是蓝牙虚拟口，别选。
插拔或换口后 COM 号会变，每次先确认。

## 参数

```
115200 / 8 / N / 1
```

## 读一次

```powershell
$p = New-Object System.IO.Ports.SerialPort
$p.PortName = "COM6"
$p.BaudRate = 115200
$p.Parity   = [System.IO.Ports.Parity]::None
$p.DataBits = 8
$p.StopBits = [System.IO.Ports.StopBits]::One
$p.Open()
$p.Write("`r")        # 发个回车，把提示符唤出来
Start-Sleep -Seconds 2
$p.ReadExisting()     # 板子回了什么
$p.Close()
```

`ReadExisting()` 只读一次会漏，要循环读几秒。完整脚本：`skill/checkers/pynq_serial_console.ps1`。

## 怎么判断读到了

正常出 `xilinx@pynq:~$`。`$` = 普通用户，`#` = root。

一片空白 = 板子没开机 / COM 号错 / 线只供电不传数据。

## 实测

- `DtrEnable` / `RtsEnable` 不用手动置高，默认 false 也有输出。网上常见写法会加这两句，这块板子不需要。
- 115200 8N1 一次成功。
- 自带自动登录，不用输密码。

## 相关

- `skill/pitfalls/pynq_direct_ethernet_windows.md`
- `skill/pitfalls/pynq_shutdown_and_power.md`
