#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""案例输出格式的公共工具：统一证据字段，避免各案例各自杜撰表头。"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from dsp import Dataset, fmt

W = 78


def banner(title: str, ds: Dataset, **meta) -> None:
    print()
    print("=" * W)
    print(title)
    print("=" * W)
    print(f"游戏版本   : {ds.version}")
    print(f"数据来源   : factoriolab/factoriolab -> public/data/dsp/data.json")
    print(f"源文件 SHA : {ds.provenance['source_sha256'][:16]}…  ({ds.provenance['source_bytes']} bytes)")
    for k, v in meta.items():
        print(f"{k:<11}: {v}")


def section(title: str) -> None:
    print()
    print(f"-- {title} " + "-" * max(0, W - len(title) - 4))


def kv(label: str, value, width: int = 26) -> None:
    print(f"  {label:<{width}} {value}")


def table(headers: Sequence[str], rows: Sequence[Sequence], aligns: Optional[Sequence[str]] = None,
          widths: Optional[Sequence[int]] = None) -> None:
    cols = len(headers)
    if widths is None:
        widths = []
        for c in range(cols):
            w = max(len(str(headers[c])), *(len(str(r[c])) for r in rows)) if rows else len(str(headers[c]))
            widths.append(min(w, 34))
    aligns = aligns or ["<"] * cols

    def line(cells):
        out = "  "
        for i, c in enumerate(cells):
            s = str(c)
            out += format(s, f"{aligns[i]}{widths[i]}") + " "
        return out.rstrip()

    print(line(headers))
    print("  " + "-" * (sum(widths) + cols - 1))
    for r in rows:
        print(line(r))


def verdict(name: str, passed: bool, detail: str = "") -> None:
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
