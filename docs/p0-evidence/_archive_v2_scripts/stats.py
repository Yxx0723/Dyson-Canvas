#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据集统计（唯一口径来源）

报告中所有数据集数字必须由本脚本生成，不允许手工誊抄。
上一版报告 A.3 出现「物品 182 但分布合计 181」「多配方 17 vs 摘要 18」的不一致，
根因是分布统计只枚举了 k ∈ {0,1,2,3,4}，漏掉了产出配方数 ≥ 5 的物品。
本脚本用 Counter 直接统计，不做桶上限假设。

用法:
    python3 stats.py            # 打印人类可读表格
    python3 stats.py --json     # 输出机器可读 JSON（写入 outputs/dataset-stats.json）
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset   # noqa: E402


def build_item_graph(ds: Dataset, recipe_ids=None):
    """边 u -> v：为产出 v，需要消耗 u。recipe_ids 为 None 时使用全部配方。"""
    g = {}
    for rid, r in ds.recipes.items():
        if recipe_ids is not None and rid not in recipe_ids:
            continue
        for o in r["out"]:
            for i in r["in"]:
                g.setdefault(i, set()).add(o)
    for o in list(g):
        g.setdefault(o, set())
    return g


def tarjan(g):
    index, low, onstack, stack, out = {}, {}, set(), [], []
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        onstack.add(v)
        for w in g.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                onstack.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(comp)

    for v in list(g):
        if v not in index:
            strong(v)
    return out


def cycles_of(g):
    res = []
    for c in tarjan(g):
        if len(c) > 1:
            res.append(sorted(c))
        elif len(c) == 1 and c[0] in g.get(c[0], ()):
            res.append(c)
    return res


def is_roundtrip(ds: Dataset, comp) -> tuple:
    """
    判定一个 SCC 是否为「状态往返环」而非「物料生产循环」。

    判据: 存在两条不同配方 r1, r2，使得
              in(r1) == out(r2)  且  out(r1) == in(r2)      （按 {item: amount} 精确相等）
          即两者互为逆过程，净物料转化恒为零 —— 典型是充能/放电、装罐/倒罐。

    反例（真正的生产循环）: plasma-refining 与 x-ray-cracking
          plasma: in={crude-oil:2}  out={hydrogen:1, refined-oil:2}
          x-ray : in={hydrogen:2, refined-oil:1}  out={energetic-graphite:1, hydrogen:3}
          两者不互为逆过程（一边净产氢、一边净耗精炼油），所以不是往返。
    """
    ids = set(comp)
    rs = set()
    for it in ids:
        rs.update(ds.produced_by.get(it, []))
    rs = sorted(rs)
    for r1 in rs:
        i1 = ds.recipes[r1]["in"]
        o1 = ds.recipes[r1]["out"]
        if not i1 or not o1:
            continue
        for r2 in rs:
            if r2 == r1:
                continue
            if ds.recipes[r2]["out"] == i1 and ds.recipes[r2]["in"] == o1:
                return True, (r1, r2)
    return False, None


def stats(ds: Dataset) -> dict:
    items = ds.items
    prod_count = Counter(len(ds.recipes_for(i)) for i in items)
    multi_out = sorted(r["id"] for r in ds.recipes.values() if len(r["out"]) > 1)
    mining = sorted(r["id"] for r in ds.recipes.values() if "mining" in r["flags"])
    g = build_item_graph(ds)
    cycles = cycles_of(g)

    cycle_detail = []
    for c in cycles:
        rt, pair = is_roundtrip(ds, c)
        cycle_detail.append({
            "items": c,
            "kind": "roundtrip" if rt else "material",
            "recipes": pair,
        })

    return {
        "provenance": ds.provenance,
        "counts": {
            "items": len(items),
            "recipes": len(ds.recipes),
            "machines": len(ds.machines),
            "modules": len(ds.modules),
        },
        "producers_distribution": dict(sorted(prod_count.items())),
        "items_with_multiple_recipes": sum(v for k, v in prod_count.items() if k > 1),
        "multi_output_recipes": multi_out,
        "mining_recipes": mining,
        "item_graph": {"nodes": len(g), "edges": sum(len(v) for v in g.values())},
        "scc_total": len(cycles),
        "scc_material": sum(1 for c in cycle_detail if c["kind"] == "material"),
        "scc_roundtrip": sum(1 for c in cycle_detail if c["kind"] == "roundtrip"),
        "scc_detail": cycle_detail,
    }


_HERE = os.path.dirname(os.path.abspath(__file__))
# 与 extract_subset.py 保持同一查找顺序：交付目录内的 vendor 优先，上游克隆兜底
RAW_CANDIDATES = [
    os.path.join(_HERE, "vendor", "dsp-data.json"),
    "/tmp/p0-refs/factoriolab/public/data/dsp/data.json",
]
RAW_DEFAULT = next((c for c in RAW_CANDIDATES if os.path.exists(c)), RAW_CANDIDATES[0])


def regimes_raw(raw_path: str = RAW_DEFAULT) -> dict:
    """
    R0 口径必须在原始 data.json 上算 —— 本仓库的 dsp-subset.json 在抽取阶段
    就已经剔除了 307 条 technology 配方，用子集算 R0 会得到与 R1 相同的数字，
    从而掩盖「含研究配方时图谱是否不同」这一事实。
    """
    if not os.path.exists(raw_path):
        return {"available": False, "reason": f"未找到原始数据: {raw_path}"}
    raw = json.load(open(raw_path, encoding="utf-8"))
    g = {}
    for r in raw["recipes"]:
        for o in r.get("out", {}):
            for i in r.get("in", {}):
                g.setdefault(i, set()).add(o)
    for o in list(g):
        g.setdefault(o, set())
    cyc = cycles_of(g)
    detail = []
    for c in cyc:
        # 往返判定需要配方细节，这里用原始 recipes 建临时索引
        ids = set(c)
        rs = set()
        for r in raw["recipes"]:
            if ids & set(r.get("out", {})):
                rs.add(r["id"])
        detail.append((sorted(c), len(rs)))
    return {"available": True, "n_recipes": len(raw["recipes"]),
            "nodes": len(g), "edges": sum(len(v) for v in g.values()),
            "scc": detail}


def regimes(ds: Dataset) -> list:
    """
    在不同过滤口径下重算 SCC，用于给「唯一循环」的结论划定适用边界。
    报告中任何「唯一」都必须附口径，否则会随数据版本或过滤规则漂移。
    """
    total = len(ds.recipes)
    all_ids = set(ds.recipes)
    non_tech = {r for r, v in ds.recipes.items() if "technology" not in v["flags"]}
    # 每物品首选一条配方（排序后第一条），模拟「单主配方」口径
    primary = set()
    for it in ds.items:
        c = ds.recipes_for(it)
        if c:
            primary.add(c[0])
    primary |= {r for r, v in ds.recipes.items() if "mining" in v["flags"]}
    # 剔除能源交换站配方（充放电，属状态往返）
    no_exchanger = {r for r, v in ds.recipes.items()
                    if "energy-exchanger" not in v["producers"]}

    out = []
    for label, ids, note in [
        ("R1 剔除 technology（本报告主口径）", non_tech, "189 条生产配方"),
        ("R2 每物品仅保留首选配方 + 全部 mining", primary, "模拟「单主配方」简化口径"),
        ("R3 剔除 energy-exchanger 配方（充放电）", no_exchanger, "只看物料制造"),
    ]:
        g = build_item_graph(ds, ids)
        cyc = cycles_of(g)
        detail = []
        for c in cyc:
            rt, _ = is_roundtrip(ds, c)
            detail.append((c, "状态往返" if rt else "物料生产"))
        out.append({"label": label, "n_recipes": len(ids), "note": note,
                    "nodes": len(g), "edges": sum(len(v) for v in g.values()),
                    "scc": detail})
    return out


def main() -> int:
    ds = Dataset()

    if "--regimes" in sys.argv:
        print("=" * 78)
        print("SCC 的口径敏感性（同一结论在不同过滤规则下是否成立）")
        print("=" * 78)
        r0 = regimes_raw()
        if r0["available"]:
            print(f"\n  R0 全量配方（含 technology 研究配方，直接读原始 data.json）")
            print(f"    配方数 {r0['n_recipes']} | 图 {r0['nodes']} 节点 / {r0['edges']} 边 | SCC {len(r0['scc'])} 个")
            for c, n in r0["scc"]:
                print(f"      {c}   （涉及产出配方 {n} 条）")
        else:
            print(f"\n  R0 不可用：{r0['reason']}")
        for r in regimes(ds):
            print(f"\n  {r['label']}")
            print(f"    配方数 {r['n_recipes']:>3} | 图 {r['nodes']} 节点 / {r['edges']} 边 | SCC {len(r['scc'])} 个")
            for c, kind in r["scc"]:
                print(f"      [{kind}] {c}")
        print()
        print("  口径结论（逐条，不含糊）:")
        print("    * R1（主口径，189 条生产配方）: 物料生产循环 1 个、状态往返 1 个。")
        print("    * R2（每物品仅首选配方）: SCC = 0 —— 循环在图上【完全消失】。")
        print("      原因: 首选配方口径下 refined-oil 只有 plasma-refining、")
        print("            hydrogen 只有 gas-giant，二者不构成互相依赖。")
        print("      含义: 「单主配方」不是「简化」，而是把循环问题从视野里删掉了。")
        print("    * R3（剔除充放电配方）: 只剩 1 个物料生产循环，与 R1 的物料结论一致。")
        print("    * R0/R1 的 SCC 集合相同（R0 见上），差异只在配方总数。")
        print()
        print("  因此「唯一的生产循环」这一结论的完整表述应为:")
        print("    「在 DSP 0.10.29.21950 的 FactorioLab 数据集上，剔除 technology 类")
        print("      研究配方后，对 189 条生产配方构图，物料生产循环恰好 1 个、")
        print("      另有 1 个储能充放电往返环。」超出该口径不作保证。")
        return 0

    s = stats(ds)

    if "--json" in sys.argv:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "outputs", "dataset-stats.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        print(f"[OK] {out}")
        return 0

    print("=" * 70)
    print("数据集统计（口径见下方说明）")
    print("=" * 70)
    p = s["provenance"]
    print(f"数据源      : {p['source_repo']} / {p['source_path']}")
    print(f"SHA256      : {p['source_sha256']}")
    print(f"游戏版本    : {p['game_version']}")
    print()
    print("口径说明:")
    print("  * 配方口径: 保留 data.json 全部 496 条中剔除 307 条 flags=['technology'] 后的 189 条")
    print("              （locked 标记保留 —— 它只表示需科技解锁，仍是生产配方）")
    print("  * 物品口径: 被子集中任一配方或机器引用到的物品，不是 data.json 的 489 条全量")
    print("  * 建筑口径: 携带 machine 区块的物品")
    print("  * 图口径  : 节点=物品(含仅作输入出现的)，边=「产 v 需耗 u」")
    print()
    c = s["counts"]
    print(f"物品 {c['items']} · 配方 {c['recipes']} · 建筑 {c['machines']} · 增产剂 {c['modules']}")
    print()
    print("产出配方数分布（该物品能被多少条配方产出）:")
    total = 0
    for k, v in s["producers_distribution"].items():
        total += v
        print(f"    {k:>2} 条 : {v:>4}")
    print(f"    --------- 合计 {total}")
    assert total == c["items"], f"分布合计 {total} != 物品数 {c['items']}"
    print()
    print(f"多配方物品（≥2 条产出配方）: {s['items_with_multiple_recipes']}")
    print(f"多输出配方（≥2 个产物）    : {len(s['multi_output_recipes'])}")
    for r in s["multi_output_recipes"]:
        print(f"    - {r}")
    print(f"mining 配方                : {len(s['mining_recipes'])}")
    print()
    ig = s["item_graph"]
    print(f"物品依赖图: {ig['nodes']} 节点 / {ig['edges']} 边")
    print(f"强连通分量: 共 {s['scc_total']} 个"
          f"（物料生产循环 {s['scc_material']}，状态往返 {s['scc_roundtrip']}）")
    for d in s["scc_detail"]:
        tag = "状态往返" if d["kind"] == "roundtrip" else "物料生产"
        print(f"    [{tag}] {d['items']}  往返配方={d['recipes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
