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

`ReadExisting()` 只读一次会漏，要循环读几秒。完整脚本：`skill/checkers/pynq_serial_console.ps1`；
要**发命令**而不只是读，用 `skill/checkers/pynq_serial_send.ps1`。

## 中文显示成全问号 `??????`

**板子没问题，是 PC 这侧三个编码默认值。** 查板子会白费功夫：

```
$ echo $LANG                          ->  en_US.UTF-8
$ python3 -c "import sys;print(sys.stdout.encoding)"  ->  utf-8
```

三个默认值：

| 哪一处 | 默认值 | 后果 |
|---|---|---|
| `SerialPort.Encoding` | ASCII | **收发双向**把非 ASCII 换成 `?` |
| `[Console]::OutputEncoding` | OEM 代码页（本机 936） | 文字到终端前又被重编一次 |
| `Get-Content` | ANSI 代码页 | 用 UTF-8 写的命令行读成乱码 |

第一条是病根。诊断方法：发一句 `print('中文测试')` 看**回显**——
回显都是 `?`，说明字符在**发出去之前**就丢了。

修法是三行：

```powershell
$utf8 = New-Object System.Text.UTF8Encoding $false
$port.Encoding = $utf8                  # 收发
[Console]::OutputEncoding = $utf8       # 显示
$lines = Get-Content -LiteralPath $f -Encoding UTF8   # 读命令行文件
```

第三条最阴：命令行里全是 ASCII 时它不暴露。一旦命令带中文，
它和第一条叠在一起，现象几乎一样。

排查纪律：**输出是乱码时，先发一句纯 ASCII 的对照，把问题切到链路某一段**，
不要在板子和脚本两端同时改。

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
