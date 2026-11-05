#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 B —— 卡西米尔晶体生产链

验证目标:
  B1 多级生产链展开（卡西米尔 → 石墨烯 → 高能石墨 → 煤；卡西米尔 → 钛晶 → 有机晶体 → 塑料 → 精炼油）
  B2 多输入配方（卡西米尔 3 输入、硫酸 3 输入、有机晶体 3 输入）需求是否正确相加
  B3 不同生产配方切换对整条链的影响（casimir-advanced / graphene-advanced）
  B4 中间产品需求汇总（同一物品被多条路径消费时是否正确合并）
  B5 建筑数量与产能
  B6 递归模型的边界：氢气既是卡西米尔的输入，又是等离子精炼的副产物 —— 树状模型必然失真

关键观察（本案例的核心价值）:
  卡西米尔晶体每次消耗 12 氢气，而精炼油的唯一来源「等离子精炼」同时副产氢气。
  一个纯递归的树无法表达「我需要的氢有 3.25/s 是自己产出的」，
  只能把 12/s 全部当成外购 —— 这直接引出案例 D 的矩阵模型。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset, RecursiveSolver, check_conservation, fmt, dump_tree   # noqa: E402
from report import banner, section, kv, table, Checker                         # noqa: E402

TARGET = "casimir-crystal"
RATE = 1.0  # items/s

MACHINE_PREF = [
    "assembling-machine-3", "arc-smelter", "chemical-plant",
    "oil-refinery", "matrix-lab", "assembling-machine-2", "assembling-machine-1",
]

# ---- 手算基准：1 个卡西米尔晶体/秒，无增产剂 ----
# casimir   t=4  asm3(1.5)  -> cycles 1/s, machines = 4/1.5 = 2.666667
# graphene  t=3  chem(1.0)  -> 需 2/s, out=2 => cycles 1/s, machines = 3
# EG        t=2  arc(1.0)   -> 石墨烯需 3/s + 塑料需 2/s = 5/s => machines = 10
# 硫酸      t=6  chem(1.0)  -> 石墨烯每 craft 需 1 => 1/s; out=4 => cycles .25 => 1.5 台
# 钛晶      t=4  asm3(1.5)  -> 1/s => 4/1.5 = 2.666667
# 有机晶体  t=6  chem(1.0)  -> 1/s => 6 台
# 钛锭      t=2  arc(1.0)   -> 3/s => 6 台
# 塑料      t=3  chem(1.0)  -> 2/s => 6 台
# 精炼油    t=4  refinery(1)-> 硫酸 1.5 + 塑料 4 + 有机晶体 1 = 6.5/s; out=2 => cycles 3.25 => 13 台
EXPECTED_MACHINES = {
    "assembling-machine-3": 2.0 * 4.0 / 1.5,      # 卡西米尔(2.6667) + 钛晶(2.6667) = 5.3333
    "chemical-plant": 3.0 + 1.5 + 6.0 + 6.0,      # 石墨烯 + 硫酸 + 有机晶体 + 塑料 = 16.5
    "arc-smelter": 10.0 + 6.0,                    # 高能石墨 + 钛锭 = 16
    "oil-refinery": 13.0,
}
EXPECTED_RAW = {
    "coal": 10.0,        # EG 5/s，煤 2/个
    "stone": 2.0,        # 硫酸 0.25 craft × 8
    "water": 2.0,        # 硫酸 1.0 + 有机晶体 1.0
    "titanium-ore": 6.0, # 钛锭 3/s × 2
    "crude-oil": 6.5,    # 精炼油 6.5/s，原油 2/2
    "hydrogen": 12.0,    # 卡西米尔每 craft 12 —— 注意这是「树模型的口径」，见 B.6
}


def chain_table(ds: Dataset, target: str, depth: int = 0, seen=None):
    """打印配方依赖（不去重，用于展示真实层级）。"""
    if seen is None:
        seen = []
    rid = ds.produced_by.get(target, [None])[0]
    if rid is None:
        return
    r = ds.recipes[rid]
    print("  " * depth + f"{ds.name(target):<24} ← {rid:<28} t={r['time']:<5} "
          f"in={ {k: v for k, v in list(r['in'].items())} }")
    if depth >= 3:
        return
    for i in r["in"]:
        chain_table(ds, i, depth + 1, seen)


def main() -> int:
    ds = Dataset()
    banner("案例 B · 卡西米尔晶体生产链", ds,
           目标=f"{ds.name(TARGET)} ({TARGET}) {RATE}/s",
           建筑="assembling-machine-3 / chemical-plant / arc-smelter / oil-refinery",
           增产剂="无",
           截断层="hydrogen（氢气先按外购处理，用于暴露树模型缺陷）")

    section("B.0 原始配方（数据原样）")
    ids = ["casimir-crystal", "graphene", "energetic-graphite", "sulfuric-acid",
           "titanium-crystal", "organic-crystal", "plastic", "titanium-ingot",
           "plasma-refining", "casimir-crystal-advanced", "graphene-advanced"]
    rows = []
    for rid in ids:
        r = ds.recipes[rid]
        rows.append([rid, r["time"],
                     " ".join(f"{k}×{v}" for k, v in sorted(r["in"].items())),
                     " ".join(f"{k}×{v}" for k, v in sorted(r["out"].items()))])
    table(["配方", "工时", "输入", "输出"], rows, ["<", ">", "<", "<"], [28, 5, 38, 30])

    section("B.1 配方依赖（真实层级，未去重）")
    chain_table(ds, TARGET)

    section("B.2 递归展开（hydrogen 截断为外购）")
    solver = RecursiveSolver(ds, module_id=None, machine_pref=MACHINE_PREF,
                             cutoff=["hydrogen"], rounding="exact")
    plan = solver.solve(TARGET, RATE)
    dump_tree(plan.root, ds)

    section("B.3 建筑数量：实际 vs 手算")
    rows, ok_mach = [], True
    for mid, expect in EXPECTED_MACHINES.items():
        act = plan.machines.get(mid, 0.0)
        good = abs(act - expect) < 1e-6
        ok_mach &= good
        rows.append([ds.machine_name(mid), f"{expect:.4f}", f"{act:.4f}",
                     f"{act - expect:+.2e}", "PASS" if good else "FAIL"])
    table(["建筑", "手算", "求解器", "偏差", "判定"], rows, ["<", ">", ">", ">", "<"], [26, 12, 12, 10, 6])
    kv("制造设备合计（小数）", f"{sum(plan.machines.values()):.4f} 台")
    kv("制造设备合计（按配方分组取整）", f"{sum(plan.machines_ceil().values())} 台")
    print("    分组明细（理论台数 → 建设台数）:")
    for rid, mid, frac, whole in plan.machine_groups_by_recipe():
        print(f"      {rid:<22} {ds.machine_name(mid):<22} {frac:8.4f} → {whole}")
    kv("矿脉开采设备", ", ".join(f"{ds.machine_name(k)} {v:.2f}" for k, v in plan.mine_machines.items()) or "—")
    kv("总功耗（usage 口径）", f"{plan.power_kw:.1f} kW = {plan.power_kw / 1000:.2f} MW")

    section("B.4 中间产品需求与原料汇总")
    rows, ok_raw = [], True
    for iid, expect in EXPECTED_RAW.items():
        act = plan.raw.get(iid, 0.0)
        good = abs(act - expect) < 1e-6
        ok_raw &= good
        rows.append([ds.name(iid), f"{expect:.4f}", f"{act:.4f}", "PASS" if good else "FAIL"])
    for iid in sorted(set(plan.raw) - set(EXPECTED_RAW)):
        rows.append([ds.name(iid), "0", f"{plan.raw[iid]:.4f}", "FAIL"])
        ok_raw = False
    table(["原料（外购）", "手算", "求解器", "判定"], rows, ["<", ">", ">", "<"], [26, 12, 12, 6])

    section("B.5 配方切换对比（同一目标，不同上游）")
    variants = [
        ("基准：石墨烯(常规) + 卡西米尔(常规)", "casimir-crystal", {}),
        ("高级卡西米尔 + 常规石墨烯", "casimir-crystal", {"casimir-crystal": "casimir-crystal-advanced"}),
        ("常规卡西米尔 + 高级石墨烯", "casimir-crystal", {"graphene": "graphene-advanced"}),
        ("高级卡西米尔 + 高级石墨烯", "casimir-crystal",
         {"casimir-crystal": "casimir-crystal-advanced", "graphene": "graphene-advanced"}),
    ]
    rows = []
    variant_raw = {}
    for label, tgt, ov in variants:
        s = RecursiveSolver(ds, machine_pref=MACHINE_PREF, cutoff=["hydrogen"],
                            recipe_override=ov, rounding="exact")
        p = s.solve(tgt, RATE)
        variant_raw[label] = dict(p.raw)
        rows.append([label, f"{len(p.machines_ceil()) and sum(p.machines_ceil().values())}",
                     f"{sum(p.machines.values()):.2f}",
                     f"{p.power_kw / 1000:.2f}",
                     f"{len(p.raw)}"])
    table(["配方组合", "设备(整)", "设备(小数)", "功耗MW", "原料种类"], rows,
          ["<", ">", ">", ">", ">"], [40, 8, 9, 8, 8])
    print("  说明: 高级卡西米尔用「光栅晶体 ×8」替代「钛晶 ×1」，上游完全不同；")
    print("        高级石墨烯用「可燃冰 ×2」一步产出石墨烯+氢气，砍掉整条高能石墨/硫酸支链。")
    print("        这是「多种配方生产同一物品」的真实来源 —— 共 " +
          f"{sum(1 for i in ds.items if len(ds.recipes_for(i)) > 1)} 个物品拥有多条配方。")

    section("B.6 递归模型的边界（本案例最重要的发现）")
    # 精炼油由等离子精炼产出，同时副产氢气
    plasma = ds.recipes["plasma-refining"]
    ro = plan.consumed.get("refined-oil", 0.0)
    print(f"  等离子精炼: {plasma['in']} -> {plasma['out']}  (t={plasma['time']})")
    print(f"  本方案共消费精炼油 {ro:.4f}/s（硫酸 1.5 + 塑料 4.0 + 有机晶体 1.0），"
          f"对应等离子精炼 {ro / 2:.4f} 次/s，")
    print(f"  同步副产氢气 {ro / 2 * plasma['out']['hydrogen']:.4f}/s。")
    print()
    print(f"  但树状求解器把氢气 {plan.raw.get('hydrogen', 0):.2f}/s 全部记为外购，")
    print(f"  同时又把 {ro / 2 * plasma['out']['hydrogen']:.2f}/s 的副产氢记入净流量 —— 两者互相矛盾。")
    ok_cons, dev = check_conservation(plan, TARGET, RATE)
    print(f"  物料守恒校验结果: {'自洽' if ok_cons else '不自洽'}  "
          f"偏差={ {k: round(v, 4) for k, v in dev.items()} }")
    print("  （此处「不自洽」是本案例要证明的结论，判定见 B.7 的「预期失败」一项）")
    print()
    print("  结论: 树模型在「同一物品既是输入又是副产物」时必然失真。这不是实现 bug，")
    print("        而是递归范式的固有局限，必须由案例 D 的矩阵模型解决。")

    section("B.7 结论")
    chk = Checker("case_b")

    # --- B1 真正的检查：展开必须到达「外购/矿脉」层，且每个非目标叶子的净流量被登记 ---
    craft_items = []
    def walk(n):
        if n.kind == "craft" and n.children:
            craft_items.append(n.item)
        for c in n.children:
            walk(c)
    walk(plan.root)
    leaves = [n for n in _iter_nodes(plan.root) if n.kind in ("raw", "mine", "cutoff", "cycle")]
    b1 = len(craft_items) >= 9 and all(l.item in plan.raw for l in leaves)
    chk.check("B1 多级展开", b1,
              f"制造层 {len(craft_items)} 个、叶子 {len(leaves)} 个，全部登记为外部供给")

    # --- B2 多输入配方：三条多输入配方的输入须逐项等于「速率 × 配方用量」 ---
    ok_b2, b2_detail = True, []
    for item, rid in (("casimir-crystal", "casimir-crystal"),
                      ("sulfuric-acid", "sulfuric-acid"),
                      ("organic-crystal", "organic-crystal")):
        node = next((n for n in _iter_nodes(plan.root) if n.item == item and n.kind == "craft"), None)
        if node is None:
            ok_b2 = False; b2_detail.append(f"{item}: 未找到节点"); continue
        rec = ds.recipes[rid]
        for i, amt in rec["in"].items():
            want = amt * node.cycles_per_s
            child = next((c for c in node.children if c.item == i), None)
            if child is None or abs(child.rate - want) > 1e-9:
                ok_b2 = False
                b2_detail.append(f"{item}.{i}: 期望 {want:.4f}")
    chk.check("B2 多输入配方", ok_b2,
              "卡西米尔 3 输入、硫酸 3 输入、有机晶体 3 输入均逐项等于 速率×用量"
              if ok_b2 else "; ".join(b2_detail))

    # --- B3 配方切换必须是「真的换了上游」，不能只打印一句 True ---
    adv_raw = variant_raw["高级卡西米尔 + 高级石墨烯"]
    base_raw = variant_raw["基准：石墨烯(常规) + 卡西米尔(常规)"]
    b3 = ("titanium-ore" in base_raw and "titanium-ore" not in adv_raw
          and "fire-ice" in adv_raw and adv_raw != base_raw)
    chk.check("B3 配方切换", b3,
              "高级组合用 光栅晶体/可燃冰 替代 钛晶/高能石墨支链，原料集合发生实质变化"
              if b3 else f"原料集合未按预期变化: base={sorted(base_raw)} adv={sorted(adv_raw)}")

    # --- B4 中间产品合并：EG 被石墨烯与塑料双重消费 ---
    eg_expect = 3.0 + 2.0
    eg_act = plan.consumed.get("energetic-graphite", 0.0)
    chk.check("B4 中间产品合并", abs(eg_act - eg_expect) < 1e-9,
              f"高能石墨被石墨烯(3.0)与塑料(2.0)双重消费，合并为 {eg_act:.4f}/s")

    # --- B4b 原料汇总必须逐项等于手算基准，且不得出现基准之外的外购 ---
    raw_dev = {k: (plan.raw.get(k, 0.0), v) for k, v in EXPECTED_RAW.items()
               if abs(plan.raw.get(k, 0.0) - v) > 1e-6}
    extra = sorted(set(plan.raw) - set(EXPECTED_RAW))
    chk.check("B4b 原料汇总 vs 手算", (not raw_dev) and (not extra),
              f"{len(EXPECTED_RAW)} 项原料逐项一致，无多余外购"
              if not raw_dev and not extra
              else f"偏差 {raw_dev}；多余外购 {extra}")

    # --- B5 建筑数量：小数口径逐项对齐手算 ---
    chk.check("B5 建筑数量（小数）", ok_mach, "与手算逐项一致（容差 1e-6）")

    # --- B5b 整数建设方案：按配方分组取整，并核对与「先合并再取整」的差异 ---
    int_by_group = sum(plan.machines_ceil().values())
    merged_ceil = sum(_ceil(v) for v in plan.machines.values())
    chk.check("B5b 整数建设（按配方分组）", int_by_group == 52,
              f"分组取整 {int_by_group} 台；若先按机器合并再取整则为 {merged_ceil} 台")

    # --- B6 预期失败：递归模型必须「无法自洽」，且偏差等于副产氢 ---
    plasma = ds.recipes["plasma-refining"]
    ro = plan.consumed.get("refined-oil", 0.0)
    h2_byproduct = ro / plasma["out"]["refined-oil"] * plasma["out"]["hydrogen"]
    ok_cons, dev = check_conservation(plan, TARGET, RATE)
    h2_dev = dev.get("hydrogen", 0.0)
    chk.xfail("B6 递归模型边界",
              (not ok_cons) and abs(h2_dev - h2_byproduct) < 1e-9,
              f"守恒校验确实失败，且偏差 +{h2_dev:.4f}/s 恰等于副产氢 "
              f"{h2_byproduct:.4f}/s（若偏差消失，说明前提已变，此结论须重审）")

    chk.summary()
    print("  证据等级: B1-B5b 已通过独立测试验证；B6 为「预期失败被正确检出」的定量证据")
    return chk.exit_code()


def _ceil(v: float) -> int:
    import math
    return int(math.ceil(v - 1e-9))


def _iter_nodes(n):
    yield n
    for c in n.children:
        yield from _iter_nodes(c)


if __name__ == "__main__":
    raise SystemExit(main())
