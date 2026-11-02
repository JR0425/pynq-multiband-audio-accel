# 本目录的 mp4 不入 git 历史

演示视频 59.5 MB，进过一次历史就删不干净，所以只挂在 Release 上：

- <https://github.com/JR0425/pynq-multiband-audio-accel/releases/tag/demo-20261101>
- 文件名 `demo_full_20261101.mp4`

**要打提交包**：把该文件下载到本目录，再跑 `python tools/make_submission_package.py`。
脚本只收本目录下的 `*.mp4`，缺了会拒绝出包（`--no-extras` 可显式跳过）。
所以在提交包里，`report/video/` 下应该是 mp4 本身，而不是这份说明。

**如果你是在提交包里看到这一行**：说明这一份没有带视频，视频需从上面的 Release 链接取。

本文件的作用是让目录在 clone 后仍然存在，不必手建。
