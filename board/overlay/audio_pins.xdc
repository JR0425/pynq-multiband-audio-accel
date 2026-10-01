## 音频通路引脚约束 —— Pynq-Z2 板上 ADAU1761 codec 的固定接法
##
## 全部抄自 PYNQ 官方 `boards/Pynq-Z2/base/base.xdc` 的 "## Audio" 那一段
## （那是这块板子上音频引脚的唯一权威来源，别按别的资料改）。
##
## 用法：由 build_audio.tcl 自动加载，不需要手工 add_files。
## 自建 overlay 第 2 步的设备就这些脚，第 3 步插核时一行都不用动。

## codec 配置总线：PS 侧 I2C1 经 EMIO 引到 PL 的这两个脚。
## PULLUP 是 I2C 的物理要求（开漏总线），少了它总线读不回来。
set_property -dict {PACKAGE_PIN U9 IOSTANDARD LVCMOS33} [get_ports IIC_1_scl_io]
set_property PULLUP true [get_ports IIC_1_scl_io]
set_property -dict {PACKAGE_PIN T9 IOSTANDARD LVCMOS33} [get_ports IIC_1_sda_io]
set_property PULLUP true [get_ports IIC_1_sda_io]

## codec 的主时钟 MCLK = 10 MHz（由 clk_wiz_10MHz 从 100 MHz 分出来）
set_property -dict {PACKAGE_PIN U5 IOSTANDARD LVCMOS33} [get_ports audio_clk_10MHz]

## I2S 三根线。BCLK/LRCLK 是 codec 的位时钟和左右声道时钟，由 PL 产生；
## sdata_o 是 PL 发给 codec 的数据，sdata_i 是 codec 送给 PL 的录音数据。
set_property -dict {PACKAGE_PIN R18 IOSTANDARD LVCMOS33} [get_ports bclk]
set_property -dict {PACKAGE_PIN T17 IOSTANDARD LVCMOS33} [get_ports lrclk]
set_property -dict {PACKAGE_PIN G18 IOSTANDARD LVCMOS33} [get_ports sdata_o]
set_property -dict {PACKAGE_PIN F17 IOSTANDARD LVCMOS33} [get_ports sdata_i]

## codec 的 I2C 从地址选择位。两个都拉高 = 从地址 0x3B
## （这是 IP 内部 `2'b11` 的默认值，也是 PYNQ 驱动 audio.py 里写死的那个地址）。
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33} [get_ports {codec_addr[0]}]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33} [get_ports {codec_addr[1]}]
