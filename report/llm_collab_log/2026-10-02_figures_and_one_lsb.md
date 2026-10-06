【任务】

把 `data/figures/` 里被引用到的三张交付图核一遍。

麦克风通路和压缩增益那两条线跑通之后，回头查这些图——README、设计报告、演示脚本
都在引用它们，但**没有人真打开看过**。这一轮要确认的是"这三张图能不能读"，
不是"脚本有没有报错"。

【提示词】

「报告里引用的那几张图，你确认过能看吗？」
「`multiband_output.wav` 每次跑完 git 都报已修改，是算法有差异吗？」
「图重做完，误差纵轴换成能直接读的单位。」

【模型回答】

AI 的第一轮结论是"三张图都生成了、脚本退出码是 0，没问题"——**把"文件生成了"
当成了"图能看"**。实际打开是三张里两张是坏的，而且坏在最显眼的地方。

第二条线是把"wav 被报已修改"当成算法差异去查。查到底是写文件的两个库
满量程缩放不同，不是算法。

【哪里错了】

· **AI 的问题：把"脚本跑通、文件生成"当成了"图能看"。**

  `multiband_comparison.png` **从生成那天起一直是张空白图**。

  纵轴用的是 `|X|/N*2`。这套刻度是给**单频正弦**定的——一个满量程正弦画出来
  正好 1.0。而这里的信号是多频语音，能量摊在几百个频率格上，单格最大只有
  **0.0244**，满量程的 **3%**。配上写死的 `plt.ylim(0, 1.5)`，两条曲线全贴在 0 上。

  用错量纲不报错，只在图上显示成一条平线——而**一条平线看起来像"信号本来就是平的"**，
  不像 bug。

· **AI 的问题：两条几乎重合的线，想用透明度区分。**

  `hls_golden_comparison.png` 里参考线 `linewidth=1.4`、实测线 `linewidth=1.0`
  加 `alpha=0.8`。两条线差 75 dB SNR，本来就重合，细的那条被盖住——**看着像只画了一条**。

  在两条重合的线上调 alpha 不起作用，因为被盖住的那条根本没露出来。
  要改成**遮挡关系**：参考线加粗到 3.0 画在**下面**当描边，实测线 1.0 画在上面，
  重合的地方就露出一圈蓝边。

· **AI 的问题：瀑布图只裁了颜色，没裁几何。**

  `spectrum_waterfall.png` 的色标原本是 matplotlib 按数据 min/max 自动定的。
  开头两帧是纯数字静音（−160 dB），把色标下半段整个浪费掉，主体挤在上半段。

  第一版修法是给 `vmin` 设下限——颜色是对了，但 3D 的**几何**没动，
  那些向下的尖刺照样竖成一堵墙，把时间轴刻度挡住。要 `np.maximum(magnitude_db, vmin)`
  把数据本身也裁掉，`set_zlim` 一起改，只裁颜色管不住几何。

· **AI 的问题：没先给"报已修改"分类，就往算法差异上查。**

  每次跑完 `multiband_baseline.py`，git 都报 `data/audio/multiband_output.wav` 已修改。
  第一反应是"算法有没有不确定性"，查了一阵子才想到先看**文件本身差多少**——
  整段最大差 **3.052e-05**，正好是**一个最低位**。到这个数量级，
  基本可以断定是写文件的舍入口径，不是算法。

  这个假警报还会自己造一个更像 bug 的现象：开头那段极低电平里，
  同一个值一个舍成 0、一个截成 −1，于是"从第几个采样开始算非静音"，
  两份文件一个给 **6907**、一个给 **96**。两个数看着差了两个数量级，
  其实来自同一个 ±1 的舍入。

  判据记下来：**先量差值，再决定查哪一层。** 差 1 个最低位就去查编码/舍入，
  差 1% 才去查算法。

· **环境的坑：本机没装 `soundfile`，脚本静默走了 scipy 回退。**

  同一个输出 wav，`soundfile` 按 **×32768** 写，scipy 回退路径按 **×32767** 写。
  两边都是"满量程"，但差一个最低位。仓库里那份是 `soundfile` 写的。

  处理方式：**这个 wav 不进版本库**，`git checkout` 还原；出图也一律基于仓库那份。
  （这条现在写在项目备忘里，因为它每次跑都会重新出现。）

· **环境的坑：Windows 控制台是代码页 936，脚本的编码守卫漏了一个。**

  `compare_golden_vs_hw.py` 没有别的脚本都有的那行 stdout 重设，
  中文输出在终端里变成乱码。补上：

  ```python
  if hasattr(sys.stdout, "reconfigure"):
      sys.stdout.reconfigure(encoding="utf-8", errors="replace")
  ```

  **同一个坑如果在多个脚本里各修一遍，就会漏。** 更好的做法是它有个共用入口，
  但这里一个脚本一个文件的结构下，只能靠"新写脚本时照着已有的抄一行"。

【怎么修正】

1. **`multiband_comparison.png`**（`src/python/multiband_baseline.py`）
   - 纵轴换 dBFS：`REF = 0.5`（满量程正弦的均方），画 `10*log10(psd/REF)`；
     范围跟着峰值走 `set_ylim(peak_db - 45, peak_db + 6)`，不再写死。
   - 估计谱从单段 FFT 周期图换成 **Welch 分段平均**（`nperseg=4096`、
     `noverlap=2048`、`scaling="spectrum"`，约 22 段平均）。
     单段周期图逐格能跳 20 dB，那点起伏比压缩器的影响还大，看不出谁压了谁。
   - 加第二个子图直接画压缩前后之差，20 Hz 以下不画。

2. **`spectrum_waterfall.png`**（`src/python/plot_spectrum_waterfall.py`）
   - 加 `soundfile` → `scipy.io.wavfile` 的回退读法（这台机器上必须走回退）。
   - 切掉开头静音并记下切了多少拍，时间轴从切点起算。
   - 纵轴换 dBFS：`full_scale = 0.5 * np.hanning(1024).sum()`；
     新增 `--db-range`（默认 60），`vmin = peak - 60`。
   - **颜色和几何一起裁**：`plot_db = np.maximum(magnitude_db, vmin)`、
     `plot_surface(..., vmin=vmin, vmax=peak)`、`set_zlim(vmin, peak + 3)`。
   - 3D 的 `tight_layout()` 换成手写边距 `subplots_adjust(left=0.01, right=0.88,
     top=0.93, bottom=0.10)`——`tight_layout` 在 3D 轴上会把刻度标签挤出画布。

3. **`hls_golden_comparison.png`**（`src/python/compare_golden_vs_hw.py`）
   - 参考线 `linewidth=3.0`、`color="tab:blue"`、`alpha=1.0`，先画；
     实测线 `linewidth=1.0`、`color="tab:orange"`、`alpha=1.0`，后画。
   - 误差纵轴从裸浮点差换成 **16 位最低位**：`err_lsb = err * 32768.0`，
     标签写 `"HLS − ref\n(16-bit LSB)"`。原来那个数是 3.469e-05，
     读者要自己心算才知道它相当于多少。

**改完的数**

| 图 | 结果 |
|---|---|
| `multiband_comparison.png` | 峰值 −23.9 dBFS；压缩前后最大差 **1.93 dB** |
| `spectrum_waterfall.png` | 色标固定铺 60 dB；开头静音已切 |
| `hls_golden_comparison.png` | 上面板 **75.4 dB SNR**、最佳位移 +0；最大单点误差 **3.469e-05 = 1.137 个最低位** |

1.137 这个数**不违反**"≤1 个最低位"的说法，两处说的不是同一件事：
`src/hls/fir_types.h` 里那条注释讲的是**定点路径的截断**与**浮点路径的四舍五入**
之间的差（两边差 ≤1 个最低位，约 −90 dB），而 1.137 是把两边的定点输出都还原成
同一个浮点参考之后量出来的**总误差**。要分开写，不能互相引用。

【沉淀】

→ `src/python/multiband_baseline.py`（dBFS + Welch）
→ `src/python/plot_spectrum_waterfall.py`（几何与颜色同时裁，去掉 `tight_layout`）
→ `src/python/compare_golden_vs_hw.py`（遮挡式画法 + LSB 纵轴 + 编码守卫）
→ 三张重做的图：`data/figures/multiband_comparison.png`、
  `data/figures/spectrum_waterfall.png`、`data/figures/hls_golden_comparison.png`

几条能搬去别的项目的：

- **"脚本跑通"不是"结果能用"。** 交付物要有人真打开看过一眼，
  尤其是图——图坏了不给任何报错。
- **纵轴刻度是给什么信号定的，要先问一句。** `|X|/N*2` 那种"满量程正弦 = 1.0"
  的刻度只对单频成立，多频语音上差两个数量级。
- **两条重合的线用遮挡，不用透明度。** 细的加透明 = 看不见。
- **3D 图上裁数据范围要同时裁颜色和几何**，`vmin` 只管颜色。
- **git 报"文件已修改"，先量差值再决定查哪一层。** 差一个最低位就去查编码/舍入，
  差百分比才去查算法。这次差点为 3e-05 去怀疑算法。
