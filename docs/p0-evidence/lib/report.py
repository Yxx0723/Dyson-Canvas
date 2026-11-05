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


class Checker:
    """
    断言收集器：唯一的退出码来源。

    为什么需要它
    ------------
    v2 的 case_b / case_d / case_g 在结尾直接 `return 0`，
    即使前面的 verdict 打印了 FAIL，一键脚本仍然报告 PASS。
    这类「日志红、仪表盘绿」的问题比断言写错更危险 —— 它会让人失去对日志的信任。

    本类的约定：
      * check()  : 普通断言，False 即失败
      * xfail()  : **预期失败**。只有当被观察对象确实按预期失败时才算通过。
                   用于「递归模型在氢气回路上必然失真」这类结论 ——
                   如果哪天它不失真了，说明前提变了，同样要报 FAIL。
      * skip()   : 环境不具备（如缺 GLPK），不计入通过也不计入失败，单独统计。
    """

    def __init__(self, title: str = ""):
        self.title = title
        self.results: List[dict] = []

    def check(self, name: str, passed, detail: str = "") -> bool:
        passed = bool(passed)
        verdict(name, passed, detail)
        self.results.append({"name": name, "kind": "check",
                             "passed": passed, "detail": detail})
        return passed

    def xfail(self, name: str, failed_as_expected: bool, detail: str = "") -> bool:
        """
        预期失败被正确检出 -> PASS。
        反之（没有失败 / 失败得不符合预期）-> FAIL。
        """
        passed = bool(failed_as_expected)
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}（预期失败）" + (f" — {detail}" if detail else ""))
        self.results.append({"name": name, "kind": "xfail",
                             "passed": passed, "detail": detail})
        return passed

    def skip(self, name: str, reason: str) -> None:
        print(f"  [SKIP] {name} — {reason}")
        self.results.append({"name": name, "kind": "skip",
                             "passed": None, "detail": reason})

    # ---- 汇总 ---------------------------------------------------------- #
    def counts(self) -> Dict[str, int]:
        c = {"pass": 0, "fail": 0, "skip": 0}
        for r in self.results:
            if r["kind"] == "skip":
                c["skip"] += 1
            elif r["passed"]:
                c["pass"] += 1
            else:
                c["fail"] += 1
        return c

    def ok(self) -> bool:
        return self.counts()["fail"] == 0

    def exit_code(self) -> int:
        return 0 if self.ok() else 1

    def summary(self) -> None:
        c = self.counts()
        print()
        print(f"  判定汇总: PASS {c['pass']} · FAIL {c['fail']} · SKIP {c['skip']}")
        if c["fail"]:
            print("  失败项: " + "; ".join(r["name"] for r in self.results
                                        if r["kind"] != "skip" and not r["passed"]))
        print(f"  总体判定: {'PASS' if self.ok() else 'FAIL'}")
        print(f"  退出码  : {self.exit_code()}")
