#!/usr/bin/env python3
"""生成提交用压缩包：git 跟踪的文件 + 本机附加材料（演示视频、海报），打包时复核关键工件 md5。

为什么不用 `git archive` / 直接压缩整个目录：
  * 直接压缩目录会把 pynq_venv/（本机 564 MB）、.chart-data-*/、dist/ 一起带进去；
  * 这两种方式都不校验位流，错了也没人发现，等评委打不开才知道。

这个脚本的做法：
  1. 文件清单只取 `git ls-tree -r HEAD` —— 仓库外的东西（venv、临时目录、未提交的草稿）天然进不来；
  2. 内容按 `git cat-file` 取 blob 字节 —— 与 clone 得到的字节一致，不受本机 core.autocrlf 影响；
  3. 演示视频与海报**故意不入库**（体积、版式会改），只从本机目录收，收没收到写进清单；
  4. 打完包重新算 md5，与 README 记录的位流 / hwh 值逐项比对，不一致直接非 0 退出；
  5. 写 MANIFEST.txt（commit、文件数、每个文件的 sha256 与大小、附加材料的取舍）一并放进包内。

用法：
    python tools/make_submission_package.py                # 产物落 dist/
    python tools/make_submission_package.py --no-extras    # 不带视频与海报
    python tools/make_submission_package.py --out D:/tmp/pack.zip

工作区有未提交改动时默认拒绝打包（避免打出一份和提交记录对不上的材料），
确需打包加 --allow-dirty。
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

# 关键工件的期望 md5：必须与 README「当前演示版」表、以及
# data/results/soak_pl_600_20261029_1031.md 里记的值一致。改位流时三处一起改。
EXPECTED_MD5 = {
    "board/overlay/fir_pl.bit": "628b75b3d7a13015a69125fb8ac2d228",
    "board/overlay/fir_pl.hwh": "7fa8e8b3b53aa8bcc9d7ecfac2f546a5",
}

# 故意不入库、但提交时要带上的本机材料。文件不存在时只警告，不静默跳过。
EXTRAS = [
    {
        "name": "演示视频",
        "glob": "report/video/*.mp4",
        "missing": "report/video/ 下没有 mp4：视频在 Release demo-20261101"
        "（demo_full_20261101.mp4），先下载再打包",
    },
    {
        "name": "海报",
        "glob": "report/poster/*.pptx",
        "missing": "report/poster/ 下没有 pptx：海报源文件不入库，从本机成品目录取，"
        "或从 git 历史 e1e1d84 取回",
    },
]

# 明确不该出现在包里的东西。清单本身来自 git，这几项只是打包后的兜底断言。
FORBIDDEN_PREFIX = (
    "pynq_venv/",
    ".venv/",
    "dist/",
    ".git/",
    "__pycache__/",
    ".chart-data-",
)


def git(*args, binary=False):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=not binary,
    ).stdout


def head_files():
    """git 跟踪的全部文件（相对路径，posix 分隔）。"""
    out = git("ls-tree", "-r", "--name-only", "-z", "HEAD", binary=True)
    return [p for p in out.decode("utf-8").split("\0") if p]


def blobs(paths):
    """按 HEAD 的 blob 逐字节取出内容；不经过工作区，因此不受 autocrlf 影响。"""
    proc = subprocess.Popen(
        ["git", "-C", str(ROOT), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        for path in paths:
            proc.stdin.write(("HEAD:" + path + "\n").encode("utf-8"))
            proc.stdin.flush()
            header = proc.stdout.readline().decode("utf-8").rstrip("\n")
            parts = header.split(" ")
            if len(parts) != 3:
                raise RuntimeError("取不到 blob：%s（%s）" % (path, header))
            size = int(parts[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)  # 每条记录末尾的换行
            yield path, data
    finally:
        proc.stdin.close()
        proc.wait()


def pad(text, width):
    """按终端显示宽度补空格（中文占 2 列），让中英混排的标签对齐。"""
    shown = sum(2 if ord(c) > 0x2E80 else 1 for c in text)
    return text + " " * max(0, width - shown)


def dirty_state():
    out = git("status", "--porcelain")
    return [ln for ln in out.splitlines() if ln.strip()]


def main():
    ap = argparse.ArgumentParser(description="生成提交用压缩包并校验关键工件")
    ap.add_argument("--out", help="输出 zip 路径（默认 dist/<仓库名>_<commit7>_<日期>.zip）")
    ap.add_argument(
        "--no-extras",
        "--no-video",
        dest="no_extras",
        action="store_true",
        help="不打包视频与海报等本机附加材料（--no-video 是旧名，保留兼容）",
    )
    ap.add_argument("--allow-dirty", action="store_true", help="工作区有未提交改动也照打")
    args = ap.parse_args()

    if dirty_state() and not args.allow_dirty:
        print("工作区有未提交改动，打包出来的内容会与 HEAD 不一致。改动了：")
        for ln in dirty_state()[:20]:
            print("  " + ln)
        print("先提交，确认无误后加 --allow-dirty 再打。")
        return 3

    commit = git("rev-parse", "--short", "HEAD").strip()
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    stamp = datetime.now().strftime("%Y%m%d")

    out = Path(args.out) if args.out else DIST / ("pynq-multiband-audio-accel_%s_%s.zip" % (commit, stamp))
    out.parent.mkdir(parents=True, exist_ok=True)

    paths = head_files()
    leaked = [p for p in paths if p.startswith(FORBIDDEN_PREFIX)]
    if leaked:
        print("以下文件本不该入库，但被 git 跟踪了，先处理再打包：")
        for p in leaked[:20]:
            print("  " + p)
        return 4

    stage = out.with_suffix("")  # 解包后的目录名，同时也是打包根目录名
    if stage.exists():
        shutil.rmtree(stage)  # 只删自己上一次的产物，dist/ 是脚本独占的目录
    stage.mkdir(parents=True)

    digests = {}
    total = 0
    for path, data in blobs(paths):
        target = stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digests[path] = (hashlib.sha256(data).hexdigest(), len(data))
        total += len(data)

    # 附加材料：故意不入库，只从本机目录收。收没收到都写清楚，不静默跳过。
    extras = []
    for spec in EXTRAS:
        got = sorted(ROOT.glob(spec["glob"]))
        if args.no_extras:
            extras.append((spec["name"], "按 --no-extras 跳过", []))
        elif got:
            extras.append((spec["name"], "已包含 %d 个" % len(got), got))
        else:
            extras.append((spec["name"], "未包含：" + spec["missing"], []))
            print("\n[警告] %s %s" % (spec["name"], spec["missing"]))

    for name, note, files in extras:
        for src in files:
            rel = src.relative_to(ROOT).as_posix()
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
            target.write_bytes(data)
            digests[rel] = (hashlib.sha256(data).hexdigest(), len(data))
            total += len(data)

    # 关键工件 md5 复核：不通过就不出包
    checks = []
    bad = []
    for rel, want in EXPECTED_MD5.items():
        got = hashlib.md5((stage / rel).read_bytes()).hexdigest() if (stage / rel).is_file() else "缺文件"
        ok = got == want
        checks.append((rel, want, got, ok))
        if not ok:
            bad.append(rel)

    manifest = stage / "MANIFEST.txt"
    lines = [
        "PYNQ-Z2 多频段音频加速器 —— 提交包清单",
        "",
        "生成时间 : %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "提交     : %s（分支 %s）" % (commit, branch),
        "文件数   : %d（含清单自身）" % (len(digests) + 1),
        "总大小   : %.1f MB" % (total / 1048576),
        "",
        "[附加材料（不入库，从本机收）]",
    ]
    for name, note, files in extras:
        lines.append("  " + pad(name, 10) + note)
        for f in files:
            lines.append("           %s" % f.relative_to(ROOT).as_posix())
    lines += [
        "",
        "[关键工件 md5 复核]",
    ]
    for rel, want, got, ok in checks:
        lines.append("  %-29s 期望 %s  实际 %s  %s" % (rel, want, got, "PASS" if ok else "FAIL"))
    lines += [
        "",
        "[文件清单]",
        "  sha256                                                            字节        路径",
    ]
    for path in sorted(digests):
        sha, size = digests[path]
        lines.append("  %s  %10d  %s" % (sha, size, path))
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if bad:
        print("\n关键工件 md5 与记录不一致，未生成压缩包：")
        for rel, want, got, _ in checks:
            if want != got:
                print("  %s\n    期望 %s\n    实际 %s" % (rel, want, got))
        print("要么位流被替换（同步改 README / soak 文档 / 本脚本），要么打包内容取错了。")
        return 2

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(stage.parent).as_posix())

    print("提交包 : %s" % out)
    print("大小   : %.1f MB（解压后 %.1f MB）" % (out.stat().st_size / 1048576, total / 1048576))
    print("文件数 : %d" % (len(digests) + 1))
    for name, note, _ in extras:
        print(pad(name, 10) + ": " + note)
    print("md5    : 两个关键工件均 PASS" if all(c[3] for c in checks) else "md5    : 有失败")
    print("清单   : 包内 MANIFEST.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
