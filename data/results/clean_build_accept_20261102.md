# 干净副本位流上板验收（2026-11-02）

11-02 上午在无本地构建缓存的 worktree 里从 HEAD 重新构建了一颗 PL 直连位流。
那颗位流的 MD5 与交付件不同（Vivado 布局布线不保证逐位复现），当时只核对了资源与时序，
没有上板。本文补上上板验收。

## 做法

1. 板上备份交付位流：`fir_pl.bit` → `fir_pl.delivery.bit`，`fir_pl.hwh` → `fir_pl.delivery.hwh`。
2. 把干净副本的产物推到 `/home/xilinx/dma_exp/`，核对 MD5：

   ```
   534a4b6075363347b43e8e22089835b0  fir_pl.bit    （交付 628b75b3d7a13015a69125fb8ac2d228）
   84d162fb14ff138ed1021a95d167a8c3  fir_pl.hwh    （交付 7fa8e8b3b53aa8bcc9d7ecfac2f546a5）
   ```

3. 跑 `bash board/probes/board_accept.sh --skip-push`（跳过推位流，探针用的就是板上这颗）。
4. 跑完换回交付位流，`systemctl restart fir-pl-webui`，读回 MD5 与服务状态。

## 结果：四项全 PASS

| 项 | 关键读数 | 结论 |
|---|---|---|
| 寄存器窗口 | ID `0x504C0002`；默认 GAIN `[256,256,256,256]`；写入 `[256,511,1019,2033]` 原样读回；斜坡 100 ms 后归零 | `REGS PASS` |
| 压缩器随参数动 | 出厂档 `GR/(ENV−THR)=0.30`；门限抬到 2.0FS 后 GR 全 0；ATK=REL=0 档峰比 0.30 | `DRC PLY PROBE PASS` |
| 双耳两套处方 | 同参数左右相对差 173/191 LSB；只改右耳后 GR 374868 vs 49819；右耳 +18 dB 后 envR/envL = 7.95 | `DUAL PROBE PASS` |
| 抓取窗口 | 零穿越测得 1525.9 Hz（写入 1500 Hz）；冻结期间指针不动；读窗口 ovf 0→0、und 0→0 | `CAP WINDOW PASS` |

汇总行：

```
  寄存器窗口        PASS
  压缩器随参数动    PASS
  双耳两套处方      PASS
  抓取窗口          PASS
```

完整输出见 `clean_build_accept_20261102.txt`。

## 边界

- 只跑了这四项验收，没有在干净位流上重跑 600 秒长跑、THD 与延迟。长跑与其余实测仍来自交付位流（MD5 `628b75b3…`）。
- 位流 MD5 不同不等于行为不同：两版的 LUT/FF/BRAM/DSP 与时序都在同一档（LUT 20497 对 20509，WNS +0.065 对 +0.090），四项功能验收读数与交付位流同一量级。
- 验收后已换回交付位流并重启服务；板子当前跑的是 `628b75b3…`，状态与验收前一致（默认参数、`voice_20261031_184837.wav` 循环）。
