# v10_fixed_nosat —— 定点 + **不限流** + 关掉累加器的饱和
#
# ⚠️ 这一格原本是想量「限流 5 + 关饱和」，但复现时**忘了传 HLS_DIRECTIVES**，
#    实际综合出来的是「不下任何指令、不限流」的版本（日志里那行
#    `==== no directives (baseline) ====` 就是证据）。
#    结果反而和 v9_fixed_nolimit 配成了一对**干净对照**，所以就把这一格
#    按实际内容留下来，另开 v11_fixed_nosat_mul5 去量本来要量的事。
#
# 和 v9_fixed_nolimit 的对照关系（同一份源码、同样不限流、DSP 都是 67）：
#   v9_fixed_nolimit   acc_t = ap_fixed<40,8,AP_TRN,AP_SAT>   ← 每个运算挂一个饱和选择器
#   这一版             acc_t = ap_fixed<40,8,AP_TRN,AP_WRAP>  ← 不挂
# 唯一的自变量就是溢出模式。
#
# 为什么敢关：
#   累加器是 65 个 16 位 × 18 位乘积之和，最坏放大 65 倍，
#   而 Q8.32 有 8 位整数位 = 256 倍余量 —— **那些选择器一次都不会触发**。
#   已验：这一版的 csim 输出与饱和版**逐位相同**（1000 点，最大差 0.0）。
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v10_fixed_nosat \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl

# 无指令：不限制任何运算的个数，让 HLS 自己铺开。
