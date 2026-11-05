#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 I —— 图节点统计的回归测试

v3 修复的缺陷：构图只为「输入物品」建键，纯输出节点从未成为键，
导致 `len(g)` 系统性少算。本案例用**合成数据**把这个缺陷钉死，
再用真实数据核对四个口径的节点数与边数。

  I1 合成用例：纯输出节点必须成为键
  I2 合成用例：无输入配方（矿脉）的产物必须成为键
  I3 合成用例：链式 A→B→C 中，终端产物 C 不能丢
  I4 断言机制：故意构造漏键图，`count_graph` 必须抛异常而不是静默返回
  I5 真实数据：四个口径的节点/边数
  I6 回归对比：旧实现（只为输入建键）在这些用例上确实会少算
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset                                          # noqa: E402
from stats import build_item_graph, count_graph, cycles_of       # noqa: E402
from report import banner, section, kv, table, Checker           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# 合成数据集：不依赖真实数据，专测构图规则
# --------------------------------------------------------------------------- #
class FakeDS:
    """只提供 build_item_graph 需要的两个属性：recipes、items。"""
    def __init__(self, recipes):
        self.recipes = recipes
        self.items = {k: {"id": k} for k in
                      {x for r in recipes.values() for x in list(r["in"]) + list(r["out"])}}


def legacy_graph(ds):
    """v2 的实现，保留用于对照 —— 证明旧版确实会少算。"""
    g = {}
    for rid, r in ds.recipes.items():
        for o in r["out"]:
            for i in r["in"]:
                g.setdefault(i, set()).add(o)
    for o in list(g):
        g.setdefault(o, set())
    return g


def main() -> int:
    ds = Dataset()
    chk = Checker("case_i")
    banner("案例 I · 图节点统计回归", ds,
           目的="钉死「纯输出节点 / 无输入配方产物」不被漏计",
           说明="合成用例 + 真实数据双轨")

    # ---------------- I.1 纯输出节点 ----------------
    section("I.1 合成用例：纯输出物品")
    # 一条链：ore → plate → gear。gear 只被生产、从不被消耗 = 纯输出节点
    fake = FakeDS({
        "plate": {"in": {"ore": 2}, "out": {"plate": 1}},
        "gear":  {"in": {"plate": 1}, "out": {"gear": 1}},
    })
    g = build_item_graph(fake)
    nodes, edges = count_graph(g)
    has_gear = "gear" in g
    old = legacy_graph(fake)
    chk.check("I1 纯输出节点建键", has_gear and nodes == 3,
              f"节点 {sorted(g)}（{nodes} 个）；旧实现只有 {sorted(old)}（{len(old)} 个）")

    # ---------------- I.2 无输入配方 ----------------
    section("I.2 合成用例：无输入配方（矿脉类）")
    fake2 = FakeDS({
        "iron-vein": {"in": {}, "out": {"iron-ore": 1}},     # 无输入
        "ingot":     {"in": {"iron-ore": 1}, "out": {"ingot": 1}},
    })
    g2 = build_item_graph(fake2)
    n2, e2 = count_graph(g2)
    chk.check("I.2 无输入配方的产物建键", "iron-ore" in g2 and n2 == 2,
              f"节点 {sorted(g2)}；边 {e2}（无输入配方不产生边）")

    # ---------------- I.3 链式终端 ----------------
    section("I.3 合成用例：链式 A→B→C 的终端")
    fake3 = FakeDS({
        "r1": {"in": {"a": 1}, "out": {"b": 1}},
        "r2": {"in": {"b": 1}, "out": {"c": 1}},
    })
    g3 = build_item_graph(fake3)
    n3, e3 = count_graph(g3)
    chk.check("I3 终端产物 C 不丢", set(g3) == {"a", "b", "c"} and e3 == 2,
              f"节点 {sorted(g3)}，边 {e3}（a→b, b→c）")

    # ---------------- I.4 断言机制 ----------------
    section("I.4 断言机制：漏键图必须抛异常")
    broken = {"a": {"b"}}          # 边指向 b，但 b 不是键
    raised = False
    try:
        count_graph(broken)
    except AssertionError:
        raised = True
    chk.check("I4 漏键被断言拦截", raised,
              "count_graph 对「边目标未建键」抛 AssertionError，不再静默少算")

    # ---------------- I.5 真实数据 ----------------
    section("I.5 真实数据：四个口径的节点/边数")
    sub = json.load(open(os.path.join(HERE, "dsp-subset.json"), encoding="utf-8"))
    raw = json.load(open(os.path.join(HERE, "vendor", "dsp-data.json"), encoding="utf-8"))

    def graph_of(recipe_dicts):
        g = {}
        for r in recipe_dicts:
            outs, ins = list(r.get("out", {})), list(r.get("in", {}))
            for o in outs:
                g.setdefault(o, set())
            for i in ins:
                g.setdefault(i, set())
            for o in outs:
                for i in ins:
                    g[i].add(o)
        return g

    r1_recipes = list(sub["recipes"].values())
    r0_recipes = raw["recipes"]
    n_tech = [r for r in r0_recipes if "technology" not in r.get("flags", [])]

    cases = [
        ("R0 全量", graph_of(r0_recipes), 481, 1253),
        ("R1 主口径", graph_of(r1_recipes), 174, 444),
        ("R1' 剔 technology（原始数据）", graph_of(n_tech), 174, 444),
    ]
    rows = []
    ok5 = True
    for label, gg, want_n, want_e in cases:
        n, e = count_graph(gg)
        good = (n == want_n and e == want_e)
        ok5 &= good
        rows.append([label, f"{n}", f"{want_n}", f"{e}", f"{want_e}", "PASS" if good else "FAIL"])
    table(["口径", "实测节点", "期望", "实测边", "期望", "判定"], rows,
          ["<", ">", ">", ">", ">", "<"], [30, 9, 7, 9, 7, 6])
    chk.check("I5 真实数据节点/边数", ok5,
              "R0 481/1253、R1 174/444，与修正后的统计一致")

    # ---------------- I.6 与旧实现对比 ----------------
    section("I.6 回归对比：旧实现到底少了多少")

    def legacy_of(recipe_dicts):
        """v2 的实现：只为输入物品建键。"""
        gg = {}
        for r in recipe_dicts:
            for o in r.get("out", {}):
                for i in r.get("in", {}):
                    gg.setdefault(i, set()).add(o)
        for o in list(gg):
            gg.setdefault(o, set())
        return gg

    rows, ok6 = [], True
    for label, wants in (("R0 全量", (r0_recipes, 481)),
                         ("R1 主口径", (r1_recipes, 174))):
        recs, want = wants
        new_n = count_graph(graph_of(recs))[0]
        old_n = len(legacy_of(recs))
        good = (new_n == want and old_n < new_n)
        ok6 &= good
        rows.append([label, f"{old_n}", f"{new_n}", f"{new_n - old_n:+d}",
                     "PASS" if good else "FAIL"])
    table(["口径", "旧实现", "修正后", "差额", "判定"], rows,
          ["<", ">", ">", ">", "<"], [30, 10, 10, 8, 6])
    chk.check("I6 旧实现确实少算", ok6,
              "两个口径下旧实现都偏小，差额即纯输出节点数；边数不变")

    chk.summary()
    print("  证据等级: I1-I4 合成用例（可独立复核）；I5-I6 真实数据（与 stats.py 同源）")
    return chk.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
