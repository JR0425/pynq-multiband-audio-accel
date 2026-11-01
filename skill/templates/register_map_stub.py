#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
---
name: register-map-stub
description: >
  Read a register table out of a markdown interface spec and generate the host-side
  accessor class plus a read-back self-test, so the driver and the spec cannot drift
  apart. Use when a custom AXI-Lite IP has a register table written in a design doc,
  when board bring-up reads back the wrong value and you want to rule out a wrong
  offset, or when adding a register and needing the Python side updated in one step.
license: MIT
compatibility: pure Python 3; input is a markdown table whose first column holds 0x offsets
metadata:
  version: "1.0.0"
  updated: "2026-11-01"
---

# 从接口契约生成主机侧调用骨架

## 什么时候用

- 自研 AXI-Lite IP 的寄存器表已经在设计文档里写好了（markdown 表格）。
- 上板读回值不对，要排除"地址抄错"这一类低级原因。
- 加了一个寄存器，想让 Python 侧跟着一起动，而不是靠记。

## 什么时候别用

- 寄存器表本身就是错的。**生成器不会纠正契约，只会忠实地把它变成代码** ——
  契约错了，生成出来的驱动一样错，而且看起来更可信。
- 需要精确的位域语义（读清零、写一触发、RO 与 W1C 混排）。
  本脚本只区分 rr / rw / ro / wo 四种访问属性，不做位域建模。
  位域复杂时用它生成骨架、手工补位操作。

## 输入长什么样

任何 markdown 表格，**第一列是 `0x` 开头的偏移**即可，其余列自动识别：

    | 偏移 | 寄存器 | 访问 | 说明 |
    |---|---|---|---|
    | `0x000` | CTRL | rw | 控制位 |
    | `0x080` | ID | ro | 固定 ID 值 |
    | `0x100` | GAIN0 | rw | 第 0 段增益 |

访问列缺省按 `rw` 处理。识别到 `bank` 字样会保留在名字里，不做合并。

## 用法

    python register_map_stub.py report/hardware_interface_spec.md -o board/scripts/regs_fir_pl.py
    python register_map_stub.py spec.md --class-name FirPlRegs --base 0x43C00000

生成物里有三样东西：一个访问器类、一份 `SELFTEST_ORDER`（读回能验的寄存器）、
一个 `selftest()` 骨架（写图样 → 读回 → 比对，遇 RO 自动跳过）。

## 为什么值得生成而不是手写

本项目踩过一次：bank0 的 `RAMP_DIV` 绝对地址是 `0x170`，曾按 `0x070` 去读，
读到恒 0，**一度误判成"寄存器没实现"**。偏移是 bank 内相对量这件事，
手写时最容易漏。把"契约 → 代码"这一步变成单向自动的，这类错就没有发生的机会。
"""

import argparse
import os
import re
import sys

RE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
RE_HEX = re.compile(r"0x([0-9A-Fa-f]{1,8})")
# 只去 markdown 的行内标记。**不要去掉下划线** —— 寄存器名里的 _ 是有意义的
# （in_r / length_r），去掉之后 IN_R 会变成 INR，和契约对不上。
CLEAN = re.compile(r"[`*]")


def split_cells(line):
    m = RE_ROW.match(line)
    if not m:
        return None
    cells = [CLEAN.sub("", c).strip() for c in m.group(1).split("|")]
    return cells


def is_sep(cells):
    return all(set(c) <= set("-: ") and c for c in cells)


def parse(path):
    regs = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            cells = split_cells(raw)
            if not cells or len(cells) < 2:
                continue
            if is_sep(cells):
                continue
            m = RE_HEX.fullmatch(cells[0].strip())
            if not m:
                m2 = RE_HEX.search(cells[0])
                if not m2:
                    continue
                m = m2
            offset = int(m.group(1), 16)

            name = cells[1].strip()
            if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_\[\] ]*$", name):
                continue
            access = "rw"
            for c in cells[1:]:
                norm = c.lower().replace("/", "").replace(" ", "")
                if norm in ("rw", "ro", "wo", "rr"):
                    access = norm
                    break
            regs.append({"offset": offset, "name": name, "access": access})

    # 去重（同一偏移出现两次时保留第一条，并在 stderr 提一句）
    seen = {}
    out = []
    for r in regs:
        if r["offset"] in seen:
            print(
                "⚠️ 偏移 0x%03X 重复（%s 与 %s），保留前者"
                % (r["offset"], seen[r["offset"]], r["name"]),
                file=sys.stderr,
            )
            continue
        seen[r["offset"]] = r["name"]
        out.append(r)
    return sorted(out, key=lambda r: r["offset"])


def py_ident(name):
    n = re.sub(r"\[(\d+)\]", r"\1", name)
    n = re.sub(r"[^0-9A-Za-z_]", "_", n)
    if n and n[0].isdigit():
        n = "_" + n
    return n.upper()


def emit(regs, class_name, base, src):
    L = []
    L.append('"""自动生成，勿手改：%s -> 本文件。' % os.path.basename(src))
    L.append("")
    L.append("重新生成：python register_map_stub.py %s" % os.path.basename(src))
    L.append('"""')
    L.append("")
    L.append("# 偏移常量：以契约表的字面值为准，不做任何换算")
    for r in regs:
        L.append("REG_%-24s = 0x%03X   # %s" % (py_ident(r["name"]), r["offset"], r["access"]))
    L.append("")
    L.append("SELFTEST_ORDER = [")
    for r in regs:
        if r["access"] in ("rw", "rr"):
            L.append("    (REG_%s, 0x%03X, %r)," % (py_ident(r["name"]), r["offset"], r["name"]))
    L.append("]")
    L.append("")
    L.append("")
    L.append("class %s:" % class_name)
    L.append('    """寄存器访问器。需要一个带 read(off) / write(off, val) 的对象。"""')
    L.append("")
    L.append("    def __init__(self, mmio, base=0x%08X):" % base)
    L.append("        self.mmio = mmio")
    L.append("        self.base = base")
    L.append("")
    L.append("    def read(self, off):")
    L.append("        return int(self.mmio.read(self.base + off))")
    L.append("")
    L.append("    def write(self, off, val):")
    L.append("        self.mmio.write(self.base + off, int(val) & 0xFFFFFFFF)")
    L.append("")
    for r in regs:
        ident = py_ident(r["name"])
        if r["access"] in ("ro", "rr"):
            L.append("    def get_%s(self):" % ident.lower())
            L.append("        return self.read(REG_%s)" % ident)
        elif r["access"] == "wo":
            L.append("    def set_%s(self, val):" % ident.lower())
            L.append("        self.write(REG_%s, val)" % ident)
        else:
            L.append("    def get_%s(self):" % ident.lower())
            L.append("        return self.read(REG_%s)" % ident)
            L.append("")
            L.append("    def set_%s(self, val):" % ident.lower())
            L.append("        self.write(REG_%s, val)" % ident)
        L.append("")
    L.append("")
    L.append("def selftest(regs):")
    L.append('    """写图样 -> 读回 -> 比对。RO 跳过；写进去读不回不同值就是地址或位宽问题。')
    L.append("")
    L.append("    先跑这个，再谈功能。它能把「偏移抄错」和「IP 没实现」分开：")
    L.append("    全表都读回同一个常数 -> 大概率是地址/总线，不是某个寄存器没做。")
    L.append('    """')
    L.append("    bad = []")
    L.append("    for off, _off2, name in SELFTEST_ORDER:")
    L.append("        old = regs.read(off)")
    L.append("        for pat in (0x5A5A5A5A, 0xA5A5A5A5, old & 0xFFFFFFFF):")
    L.append("            regs.write(off, pat)")
    L.append("            got = regs.read(off)")
    L.append("            if got != (pat & 0xFFFFFFFF):")
    L.append("                bad.append((name, hex(off), hex(pat), hex(got)))")
    L.append("                break")
    L.append("        regs.write(off, old)")
    L.append("    if bad:")
    L.append("        print('读回不一致 %d 项：' % len(bad))")
    L.append("        for name, off, want, got in bad:")
    L.append("            print('  %-12s %s 期望 %s 实得 %s' % (name, off, want, got))")
    L.append("        return 1")
    L.append("    print('读回全部通过（%d 项可写寄存器）' % len(SELFTEST_ORDER))")
    L.append("    return 0")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="markdown 寄存器表 -> Python 访问器")
    ap.add_argument("spec", help="含寄存器表的 markdown 文件")
    ap.add_argument("-o", "--out", help="输出 .py（缺省打印到屏幕）")
    ap.add_argument("--class-name", default="RegMap", help="生成的类名")
    ap.add_argument("--base", default="0x43C00000", help="基地址，默认 0x43C00000")
    args = ap.parse_args()

    if not os.path.isfile(args.spec):
        print("找不到文件: %s" % args.spec)
        return 2

    regs = parse(args.spec)
    if not regs:
        print("没解析出任何寄存器行。检查表格第一列是否是 0x 开头的偏移。")
        return 1

    code = emit(regs, args.class_name, int(args.base, 16), args.spec)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(code)
        print("生成 %d 个寄存器 -> %s" % (len(regs), args.out))
    else:
        print(code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
