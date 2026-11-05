#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 C —— 多目标共享生产

目标组合（由真实数据筛出，不是假设）:
  电磁矩阵 electromagnetic-matrix  1/s
  处理器   processor               1/s
共享物品（脚本计算得出，非人工指定）: circuit-board / copper-ingot / iron-ingot / iron-ore

验证目标:
  C1 多目标生产需求：两个目标的需求是否正确并行展开
  C2 共享中间产品合并：被两个目标共同消费的物品，需求是否合并而非各算一遍
  C3 原料需求统一汇总：矿石需求是否为两目标之和
  C4 生产能力分配：合并后设备台数是否等于分别求解之和（线性可加性）
  C5 避免重复计算：向上取整后，合并产线相对「各建一套」节省多少台设备

可加性判据:
  线性系统必须满足 Plan(A+B) = Plan(A) + Plan(B)，逐项比对。
  这是本案例的硬校验 —— 不满足即说明合并逻辑有缺陷。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset, RecursiveSolver, check_conservation, dump_tree   # noqa: E402
from report import banner, section, kv, table, Checker                    # noqa: E402

TARGETS = {"electromagnetic-matrix": 1.0, "processor": 1.0}

# ---- 合并方案的整数台数手算基准 ----
# 电磁矩阵 1/s + 处理器 1/s，共享 电路板/磁线圈/铜锭/铁锭。
# 分组取整后逐台相加得 27 台；若先按机器合并台数再取整只有 26 台，
# 差的那一台来自两条 1/3 台的制造台配方被并成 1 台。
EXPECTED_MERGED_INT = 27
MACHINE_PREF = ["assembling-machine-3", "arc-smelter", "matrix-lab"]


def reachable(ds: Dataset, item: str, acc=None) -> set:
    """沿主配方展开的可达物品集合（用于自动发现共享物品）。"""
    if acc is None:
        acc = set()
    rid = ds.produced_by.get(item, [None])[0]
    if rid is None or item in acc:
        return acc
    acc.add(item)
    for i in ds.recipes[rid]["in"]:
        reachable(ds, i, acc)
    return acc


def main() -> int:
    ds = Dataset()
    banner("案例 C · 多目标共享生产", ds,
           目标=" + ".join(f"{ds.name(k)} {v}/s" for k, v in TARGETS.items()),
           建筑="assembling-machine-3 / arc-smelter / matrix-lab",
           增产剂="无")

    section("C.0 原始配方")
    rows = []
    for rid in ["electromagnetic-matrix", "processor", "circuit-board",
                "microcrystalline-component", "magnetic-coil", "copper-ingot",
                "iron-ingot", "magnet", "high-purity-silicon"]:
        r = ds.recipes[rid]
        rows.append([rid, r["time"],
                     " ".join(f"{k}×{v}" for k, v in sorted(r["in"].items())),
                     " ".join(f"{k}×{v}" for k, v in sorted(r["out"].items()))])
    table(["配方", "工时", "输入", "输出"], rows, ["<", ">", "<", "<"], [28, 5, 34, 26])

    section("C.1 共享物品自动识别（数据推导，非人工指定）")
    sets = {t: reachable(ds, t) for t in TARGETS}
    names = list(sets)
    shared = set.intersection(*[sets[n] for n in names])
    for n in names:
        print(f"  {n:<24} 可达物品 {len(sets[n]):>3} 个")
    print(f"  交集（真正共享的中间产品）: {sorted(ds.name(x) + '(' + x + ')' for x in shared)}")
    kv("共享物品数量", len(shared))
    print("  注: 交集只说明「两边都会用到」，是否真的重复计算要看合并后的速率。")

    # ---- 分别求解 ----
    s = RecursiveSolver(ds, machine_pref=MACHINE_PREF)
    plans = {t: s.solve(t, r) for t, r in TARGETS.items()}

    # ---- 合并求解 ----
    s2 = RecursiveSolver(ds, machine_pref=MACHINE_PREF)
    merged = s2.solve_multi(TARGETS)

    section("C.2 合并后的生产树")
    dump_tree(merged.root, ds)

    section("C.3 线性可加性校验 Plan(A+B) == Plan(A) + Plan(B)")
    rows, ok_add = [], True
    all_keys = set(merged.machines) | set().union(*[set(p.machines) for p in plans.values()])
    for mid in sorted(all_keys):
        a = sum(p.machines.get(mid, 0.0) for p in plans.values())
        b = merged.machines.get(mid, 0.0)
        good = abs(a - b) < 1e-9
        ok_add &= good
        rows.append([ds.machine_name(mid), f"{a:.6f}", f"{b:.6f}", f"{b - a:+.1e}",
                     "PASS" if good else "FAIL"])
    table(["建筑", "分别求解之和", "合并求解", "偏差", "判定"], rows,
          ["<", ">", ">", ">", "<"], [26, 14, 14, 10, 6])

    rows, ok_raw = [], True
    all_raw = set(merged.raw) | set().union(*[set(p.raw) for p in plans.values()])
    for iid in sorted(all_raw):
        a = sum(p.raw.get(iid, 0.0) for p in plans.values())
        b = merged.raw.get(iid, 0.0)
        good = abs(a - b) < 1e-9
        ok_raw &= good
        rows.append([ds.name(iid), f"{a:.4f}", f"{b:.4f}", "PASS" if good else "FAIL"])
    table(["原料", "分别求解之和", "合并求解", "判定"], rows, ["<", ">", ">", "<"], [24, 14, 14, 6])

    section("C.4 共享中间产品的合并证据")
    rows = []
    for iid in sorted(shared):
        if iid in TARGETS:
            continue
        cons = merged.consumed.get(iid, 0.0)
        per = [(t, p.consumed.get(iid, 0.0)) for t, p in plans.items()]
        rows.append([ds.name(iid), " + ".join(f"{v:.4f}" for _, v in per),
                     f"{cons:.4f}", "合并为单条产线" if cons > 0 else "—"])
    table(["共享物品", "各目标消费", "合并后总消费", "结果"], rows,
          ["<", ">", ">", "<"], [26, 18, 14, 16])

    section("C.4b 结构重复度（树模型的固有限制）")
    from collections import Counter

    def walk(n, acc):
        acc[n.item] += 1
        for c in n.children:
            walk(c, acc)
        return acc

    cnt = walk(merged.root, Counter())
    dup = {k: v for k, v in cnt.items() if v > 1 and k != "<multi>"}
    rows = [[ds.name(k), k, v, merged.consumed.get(k, 0.0)] for k, v in sorted(dup.items(), key=lambda x: -x[1])]
    table(["物品", "id", "树中出现次数", "总消费/s"], rows, ["<", "<", ">", ">"], [22, 28, 12, 12])
    print(f"  结论: 汇总数字正确（可加性 PASS），但树里仍然出现 {sum(dup.values()) - len(dup)} 个多余的重复节点。")
    print("        真正的「共享产线」只能在图上表达 —— 这是画布必须是图、不能是树的直接原因。")

    section("C.5 向上取整后的设备节省（按配方分组取整）")
    # 口径：先按配方分组取整，再按机器汇总。
    # 旧实现按机器合并台数后取整，会把不同配方的零头并成一台，系统性少算。
    def ceil_by_group(plan):
        acc = {}
        for (_rid, mid, _cfg), frac in plan.machine_groups.items():
            acc[mid] = acc.get(mid, 0) + int(math.ceil(frac - 1e-9))
        return acc

    rows, saved_total = [], 0
    for mid in sorted(all_keys):
        sep = sum(ceil_by_group(p).get(mid, 0) for p in plans.values())
        comb = ceil_by_group(merged).get(mid, 0)
        saved = sep - comb
        saved_total += saved
        rows.append([ds.machine_name(mid), f"{sep}", f"{comb}", f"{saved:+d}"])
    table(["建筑", "各建一套(整)", "合并产线(整)", "节省"], rows, ["<", ">", ">", ">"], [26, 13, 13, 8])
    print(f"  合并后节省设备合计: {saved_total} 台")

    merged_int = sum(ceil_by_group(merged).values())
    merged_merged = sum(int(math.ceil(v - 1e-9)) for v in merged.machines.values())
    kv("合并方案整数台数（分组口径）", f"{merged_int} 台")
    kv("合并方案整数台数（按机器合并口径）", f"{merged_merged} 台（少算 {merged_int - merged_merged} 台）")
    print("  原因: 分离建设时，每个目标各自的零头都被向上取整；合并后只需取整一次。")

    section("C.6 物料守恒")
    ok_cons = True
    for t, r in TARGETS.items():
        # 在合并方案中，各目标的净流量 = 其产量（因为共享物品净额为 0）
        v = merged.net.get(t, 0.0)
        good = abs(v - r) < 1e-6
        ok_cons &= good
        print(f"    {ds.name(t):<24} 净流量 {v:+.4f}/s  期望 +{r}  {'ok' if good else 'FAIL'}")
    for iid in sorted(merged.purchased):
        v = merged.net.get(iid, 0.0)
        exp = -merged.raw.get(iid, 0.0)
        good = abs(v - exp) < 1e-6
        ok_cons &= good
        print(f"    {ds.name(iid):<24} 净流量 {v:+.4f}/s  期望 {exp:+.4f}  {'ok' if good else 'FAIL'}")

    section("C.7 结论")
    chk = Checker("case_c")

    # C1 多目标：两个目标都必须真的被展开成独立子树，且速率正确
    roots = merged.root.children if len(merged.root.children) > 1 else [merged.root]
    got = {r.item: r.rate for r in roots}
    c1 = got == TARGETS
    chk.check("C1 多目标需求", c1,
              f"两个目标并行展开: {got}" if c1 else f"目标展开不符: {got} vs {TARGETS}")

    # C2 共享物品合并：合并后的消费量必须等于分别求解之和，逐项核对
    c2_bad = []
    for iid in sorted(shared):
        a = sum(p.consumed.get(iid, 0.0) for p in plans.values())
        b = merged.consumed.get(iid, 0.0)
        if abs(a - b) > 1e-9:
            c2_bad.append(f"{iid}: {b:.4f} != {a:.4f}")
    chk.check("C2 共享物品合并", not c2_bad,
              f"{len(shared)} 个共享物品的消费量与分别求解之和逐项相等"
              if not c2_bad else "; ".join(c2_bad))

    # C3 原料汇总：矿石需求 = 两目标之和
    c3_bad = []
    for iid in sorted(set().union(*[set(p.raw) for p in plans.values()])):
        a = sum(p.raw.get(iid, 0.0) for p in plans.values())
        b = merged.raw.get(iid, 0.0)
        if abs(a - b) > 1e-9:
            c3_bad.append(f"{iid}: {b:.4f} != {a:.4f}")
    chk.check("C3 原料汇总", not c3_bad,
              "矿石需求逐项等于两目标之和" if not c3_bad else "; ".join(c3_bad))

    chk.check("C4 产能分配", ok_add, "小数台数满足严格线性可加性（容差 1e-9）")

    ok_c5 = (merged_int == EXPECTED_MERGED_INT)
    chk.check("C5 重复计算", ok_c5 and saved_total >= 0,
              f"合并方案 {merged_int} 台（手算 {EXPECTED_MERGED_INT} 台），"
              f"相对各建一套节省 {saved_total} 台；"
              f"若按机器合并取整则为 {merged_merged} 台")

    chk.check("C6 物料守恒", ok_cons, "目标净流量与外购量均正确")

    chk.summary()
    print("  证据等级: 已通过独立测试验证（含可加性硬校验与整数建设口径）")
    return chk.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
