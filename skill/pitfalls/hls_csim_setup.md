# Vitis HLS 跑 C 仿真（batch 模式）的三个卡点

实测：Vitis HLS 2020.2，Windows 批处理模式，2026-09-28。

## 一、`open_project` 要的是工程名，不是路径

```
open_project -reset D:/proj/build/hls/csim_proj     # 报错
```

```
ERROR: [HLS 200-70] The name 'D:/proj/build/hls/csim_proj' contains illegal character ':'
```

冒号和斜杠都非法。**先 `cd` 到目标目录，再给工程名**：

```tcl
cd $script_dir
open_project -reset csim_proj
```

工程目录仍然是 `$script_dir/csim_proj`，效果一样。

## 二、csim 的工作目录是它自己临时建的，相对路径到不了仓库根

`csim_design` 会把程序拷到一个临时目录里编译运行，所以测试台里写
`data/audio/test_input.txt` 这种相对路径会找不到文件。

**用 `-argv` 把绝对路径传进去**。Tcl 在 Windows 上给的是反斜杠路径，
传进去之前换成正斜杠：

```tcl
proc unixify {p} {
    return [string map {\\ /} $p]
}

csim_design -argv "[unixify $in_file] [unixify $out_file]"
```

完整可跑的脚本见 `build/hls/run_csim.tcl`。

## 三、先拿本机 g++ 验语法，再上 HLS

本机装了 MinGW g++ 8.1.0：

```
E:\vscodedowmload\mingw64\bin\g++.exe
```

纯语法错误用 g++ 编一遍 **3 秒**就能看出来，而 HLS 走一遍要 9 秒以上
（启动 + 建工程 + 编译）。改代码的来回里这个差别很可观：

```
g++ -fsyntax-only -std=c++11 src/hls/fir_multiband.cpp
```

注意：g++ 只能验语法和基本的 C++ 错误，**报不出来 HLS 特有的问题**
（例如未展开的循环、不支持的数据类型）。它能过不等于 HLS 能过。

## 顺带：切编译开关用 `-cflags`

同一个 tcl 脚本靠环境变量切换 `-D` 开关，不用改脚本：

```tcl
set cflags ""
if {[info exists ::env(HLS_CFLAGS)]} {
    set cflags $::env(HLS_CFLAGS)
}
add_files $src_file -cflags $cflags
```

调用（Git Bash）：

```
MSYS_NO_PATHCONV=1 HLS_CFLAGS=-DFIR_PARTIAL=5 \
  "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_csim.tcl
```

`MSYS_NO_PATHCONV=1` 不能省 —— 否则 Git Bash 会把 `-D` 后面的路径风格参数改写掉。

用 `cmd //c "..."` 包一层在 Git Bash 里**跑不起来**（只打印 Windows 抬头就退出，
退出码还是 0，看不出来）。**直接调 `.bat`**。

## 相关

- 综合与优化指令：`skill/pitfalls/hls_synthesis_and_directives.md`
- 系数与数据的采样率一致性：`skill/pitfalls/audio_data_consistency.md`
