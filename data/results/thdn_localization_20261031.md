# THD+N 定位：减法式压缩的死区（2026-10-31，PYNQ-Z2 板上）

位流 `628b75b3d7a13015a69125fb8ac2d228`，服务 `fir_pl_live.py` 在线。
音源 `wav/tone1001_lo.wav`（1 kHz 正弦，输入峰值 1000 计数），探针 `board/probes/thdn.py` × 1024 点 FFT。

## 六组对照

| # | 压缩器 | 限幅器 | 输出峰值 | THD+N |
|---|---|---|---|---|
| 1 | 关 | 不动作 | — | 0.07% |
| 2 | 关（THR 抬到 0 dBFS） | 动作 | — | 7.72% |
| 3 | +30 dB / −10 dBFS / k 0.5 | 动作 | — | 9.05% |
| 4 | +30 dB / −20 dBFS / k 0.7 | 动作 | — | 16.29% |
| 5 | +24 dB / −40 dBFS / k 0.5，atk 5 ms rel 100 ms | 不动作 | — | 37.69% |
| 6 | 同 5，atk 50 ms rel 300 ms | 不动作 | 9497 | 33.94% |

谐波（dBc，相对基波）：第 2 组 3 次 −24.4；第 5 组 3 次 −10.7；第 6 组 3 次 −11.7、5 次 −21.0、
7 次 −36.2、9 次 −37.0。偶次全部低于 −80 dBc。

## 判读

1. 第 1 组等于输入本底 0.069%，定点前端链不产生失真（T13 不触发）。
2. 失真随压缩深度单调上升。
3. attack/release 放慢 10 倍几乎不改变结果，包络纹波假设排除。
4. 机制：`src/rtl/fir_drc4.v:262-266` 的 `y = sign(a)·max(0, |a| − gr)` 在零交叉处留下宽度约 2G
   的死区，折点产生奇次谐波。
5. 离线复算（A=15849，G=6352，与实测峰值 9497 对齐）得 33.9% / 三次 −12.4 dBc，与实测同量级。

## 独立互证（演示默认参数）

同一块板上另做了一组演示默认（22 dB / −20 dBFS / ratio 0.7）的独立测量：THD+N 9.13%，
3/5/7/9 次 −24.7/−29.5/−33.0/−35.8 dBc，并用 A=12589、G=1492 的同一模型逐项复现到 0.1 dB。
把段增益降到 14 dB 让包络正好压在门限上，得到"一压就出谐波、退到门限下就回到 −90 dBc"，
门限由此确认成开关。两组数据不矛盾：本文件的 37.7% 是深压，9% 是常态工作点。

## 复现

```bash
curl -X POST http://192.168.2.99:8080/api/tune -H "Content-Type: application/json" \
  -d '{"gain_db":[24,24,24,24],"thr_db":[-40,-40,-40,-40],"ratio":[0.5,0.5,0.5,0.5],"attack_ms":[5,5,5,5],"release_ms":[100,100,100,100]}'
MSYS_NO_PATHCONV=1 python board/probes/board_ssh.py --sudo \
  "cd /home/xilinx/dma_exp && XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u thdn.py --wav wav/tone1001_lo.wav --loop 4 --tag drc_hard"
```

`/api/tune` 会把没带的字段重置成默认值，每次 POST 都要发全四个参数。
测完恢复演示默认 `+22 / −20 / 0.7 / 5 ms / 100 ms`，并把 `/dev/shm/fir_pl_play.txt` 写回 `off`。
