#!/usr/bin/env python3
"""把 PYNQ 自研音频 IP `audio_codec_ctrl` 的源码拉到本地 IP 仓库。

为什么需要这个脚本
------------------
我们自建的 overlay 要能放出声，就得把 PYNQ 官方那个音频 IP
(`xilinx.com:user:audio_codec_ctrl:1.0`) 摆进 Vivado 的 IP 仓库。
它不在 Vivado 自带库里，只能从 Xilinx/PYNQ 仓库取。

为什么不直接把源码放进 git 仓库
--------------------------------
那是第三方的 500 多 KB 源码（Pynq-Z2 官方 base overlay 用的就是它），
放进公开仓库既臃肿、又容易和上游版本悄悄对不上。
所以落在 `build/` 下（已被 .gitignore 忽略），由本脚本保证随时能重新拉一份、
而且拉到的是**同一版**。

用法
----
    python board/overlay/fetch_audio_ip.py

默认落到 `build/vivado/ip_repo/audio_codec_ctrl_v1.0/`，
`build_audio.tcl` 会自动去那里找，不用手工指定。

网络说明
--------
本机 `github.com` 被 IP 层封锁，但 `api.github.com` 和 `raw.githubusercontent.com`
是通的 —— 本脚本只走这两个，不碰 github.com。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "Xilinx/PYNQ"
BRANCH = "master"
SUBDIR = "boards/ip/audio_codec_ctrl_v1.0"
API = "https://api.github.com/repos/{repo}/contents/{path}?ref=" + BRANCH
RAW = "https://raw.githubusercontent.com/{repo}/" + BRANCH + "/{path}"

# 这几个文件是 component.xml 里点名要的，少一个 Vivado 就建不了 IP
REQUIRED = [
    "component.xml",
    "src/user_logic.vhd",
    "src/iis_ser.vhd",
    "src/iis_deser.vhd",
    "src/i2s_ctrl.vhd",
    "src/axi_lite_ipif.vhd",
    "src/address_decoder.vhd",
    "src/slave_attachment.vhd",
    "src/pselect_f.vhd",
    "src/common_types.vhd",
    "src/family_support.vhd",
]


def fetch(url, timeout):
    req = urllib.request.Request(url, headers={
        # GitHub API 不收没有 User-Agent 的请求
        "User-Agent": "pynq-audio-accel-fetch-ip",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def walk(remote_dir, local_dir, dest_root, timeout):
    """递归下载 remote_dir 下的所有文件，保留目录结构。"""
    listing = json.loads(fetch(API.format(repo=REPO, path=remote_dir), timeout))
    if isinstance(listing, dict):          # 出错时 API 返回的是一个 dict，不是 list
        raise RuntimeError(f"列目录失败 {remote_dir}: {listing.get('message')}")

    for entry in listing:
        rel = os.path.relpath(entry["path"], SUBDIR)
        target = os.path.join(dest_root, rel)
        if entry["type"] == "dir":
            walk(entry["path"], entry["name"], dest_root, timeout)
        else:
            data = fetch(RAW.format(repo=REPO, path=entry["path"]), timeout)
            if len(data) != entry["size"]:
                raise RuntimeError(
                    f"{entry['path']} 字节数对不上：期望 {entry['size']}，实际 {len(data)}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
            print(f"  {rel:28s} {len(data):>8d} B")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    default_dest = os.path.join(repo_root, "build", "vivado", "ip_repo",
                                "audio_codec_ctrl_v1.0")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=default_dest, help="落到哪个目录")
    ap.add_argument("--timeout", type=int, default=60, help="单个请求超时（秒）")
    args = ap.parse_args()

    print(f"IP 仓库 -> {args.dest}")
    os.makedirs(args.dest, exist_ok=True)

    walk(SUBDIR, SUBDIR, args.dest, args.timeout)

    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(args.dest, f))]
    if missing:
        print("\n缺文件，Vivado 建不出 IP：", file=sys.stderr)
        for f in missing:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\n完成：{len(REQUIRED)} 个关键文件都在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
