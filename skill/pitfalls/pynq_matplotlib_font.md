# 板子上画图：中文字体只能二选一

在 PYNQ 板子上用 matplotlib 画图时，中文和数字不能同时正常显示。
实测：PYNQ-Z2 + PYNQ 2.7.0，matplotlib 3.1.2，2026-09-27。

## 现象

图里的中文变成一个一个方框 □□□。matplotlib 只打警告，不报错：

```
RuntimeWarning: Glyph 24405 missing from current font.
```

**跑通脚本、nbconvert 也不报错，不真的看一眼图就发现不了。**

## 原因

板子上带中文字形的字体只有一个：

```
$ fc-list :lang=zh
/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf: Droid Sans Fallback:style=Regular
```

而 matplotlib 3.1 **不会在同一个字符串里逐字回退**，两个字体只能二选一：

| 设置 | 结果 |
|---|---|
| 默认 `DejaVu Sans` | 数字、英文正常；中文变方框 |
| 改设 `Droid Sans Fallback` | 中文正常；数字、英文变方框 |

## 做法

**图里的文字一律用英文。** 坐标轴刻度全是数字，数字变方框比中文变方框更糟。

```python
plt.title("w2_tone440.wav, first 50 ms -- should be a clean sine")
plt.xlabel("time (s)"); plt.ylabel("sample value")
```

notebook 正文（markdown 格）和 `print()` 输出用中文没问题，那些不走 matplotlib。

## 验收标准

**把图拉回本地打开看，不是"脚本没报错"。**

```
python skill/checkers/pynq_jupyter_files.py get w2_audio_playback.ipynb out.ipynb
```

ipynb 里的图是 base64 PNG，解出来存成 `.png` 直接看。同一次运行里，
中文标好的同时数字可能已经坏了，只检查其中一样等于没检查。
