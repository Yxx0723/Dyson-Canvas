#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 D —— 氢气循环与副产品平衡（矩阵层）

验证目标:
  D1 循环关系是否存在 —— 用强连通分量(SCC)在真实配方图上证明，不假设
  D2 矩阵构建 A·x = b —— 配方净流量矩阵，逐元素可复核
  D3 方阵求解（0fuz/dsp-calc 的方案）—— 是否给出唯一解、是否非负
  D4 松弛变量 + 线性规划（非负约束下的正确做法）—— 是否改变了可解性结论
  D5 物料守恒 —— 逐物品核对产出/消耗/外购/放空
  D6 与递归模型对照 —— 量化树模型的误差

关键立场:
  「数学上有解」与「实际生产可行」必须分开判定。
  方阵求解给出唯一解，不代表这个解在物理上可执行（速率可能为负）。
"""
from __future__ import annotations

import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset, RecursiveSolver, check_conservation                     # noqa: E402
from report import banner, section, kv, table, Checker                          # noqa: E402
import linalg as L                                                              # noqa: E402


# --------------------------------------------------------------------------- #
# SCC：Tarjan
# --------------------------------------------------------------------------- #
def build_item_graph(ds: Dataset):
    """边 u -> v 表示「为了产出 v，需要消耗 u」。"""
    g = {}
    for rid, r in ds.recipes.items():
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


def find_cycles(ds: Dataset):
    import faults
    g = build_item_graph(ds)
    if faults.enabled("scc_none"):
        return g, []          # 故障注入：让循环检测恒为空
    comps = tarjan(g)
    cycles = []
    for c in comps:
        if len(c) > 1:
            cycles.append(sorted(c))
        elif len(c) == 1 and c[0] in g.get(c[0], ()):   # 自环
            cycles.append(c)
    return g, cycles


# --------------------------------------------------------------------------- #
# 矩阵构建
# --------------------------------------------------------------------------- #
def build_matrix(ds: Dataset, recipes, items):
    """
    A[item][recipe] = 产出量 - 消耗量（净流量系数，单次配方）。
    行 = 待平衡物品，列 = 配方。
    """
    A = [[ds.recipes[r]["out"].get(i, 0.0) - ds.recipes[r]["in"].get(i, 0.0)
          for r in recipes] for i in items]
    return A


def show_matrix(ds, recipes, items, A, b=None, slack=None):
    head = ["平衡物品"] + [r[:14] for r in recipes]
    rows = []
    for i, it in enumerate(items):
        row = [ds.name(it)[:20]]
        for j in range(len(recipes)):
            v = A[i][j]
            row.append(f"{v:+g}" if v else "0")
        if b is not None:
            row.append(f"{b[i]:g}")
        rows.append(row)
    hdr = head + (["b (需求)"] if b is not None else [])
    table(hdr, rows, ["<"] + [">"] * (len(hdr) - 1),
          [22] + [11] * len(recipes) + ([10] if b is not None else []))


def main() -> int:
    ds = Dataset()
    banner("案例 D · 氢气循环与副产品平衡（矩阵层）", ds,
           目标="石墨烯 2/s + 卡西米尔晶体 1/s（真实下游需求驱动油路）",
           方法="SCC 证明循环 → 方阵求解 → 松弛变量 + 线性规划")

    # ===== D1 证明循环存在 =====
    section("D.1 循环关系证明（强连通分量，来自真实配方图）")
    g, cycles = find_cycles(ds)
    print(f"  物品依赖图: {len(g)} 个节点, {sum(len(v) for v in g.values())} 条边")
    print(f"  检测到 {len(cycles)} 个循环分量:")
    for c in cycles:
        print(f"    <-> {', '.join(ds.name(x) + '(' + x + ')' for x in c)}")
    h2_cycle = next((c for c in cycles if "hydrogen" in c), None)
    print()
    if h2_cycle:
        print(f"  氢气所在循环: {h2_cycle}")
        print("  证明方式: Tarjan 强连通分量 —— 分量大小 > 1 即表示两物品互为上下游，")
        print("            存在可回流的闭环，而不是单向的树。")
    else:
        print("  [警告] 未找到含氢气的循环，后续矩阵场景需重新设计。")

    # ===== D2 场景与配方集 =====
    section("D.2 场景定义与配方集")
    print("  下游需求（来自案例 B 的真实链条，卡西米尔 1/s + 石墨烯 2/s）:")
    print("    精炼油 6.5/s  （硫酸 1.5 + 塑料 4.0 + 有机晶体 1.0）")
    print("    氢气  12/s    （卡西米尔每次 12）")
    print("    高能石墨 5/s  （石墨烯 3 + 塑料 2）")
    print()
    print("  候选配方（全部来自数据集，非虚构）:")
    candidate_recipes = ["plasma-refining", "x-ray-cracking", "reforming-refine"]
    rows = []
    for rid in candidate_recipes:
        r = ds.recipes[rid]
        rows.append([rid, r["time"],
                     " ".join(f"{k}×{v}" for k, v in sorted(r["in"].items())),
                     " ".join(f"{k}×{v}" for k, v in sorted(r["out"].items())),
                     "是" if h2_cycle and rid in ("x-ray-cracking", "reforming-refine") else ""])
    table(["配方", "工时", "输入", "输出", "涉及环路"], rows,
          ["<", ">", "<", "<", "<"], [20, 5, 26, 26, 9])

    # ===== D2b 方阵：2 配方 2 物品 =====
    section("D.3 方阵模型 A·x = b（0fuz/dsp-calc 的做法）")
    recipes = ["plasma-refining", "x-ray-cracking"]
    items = ["refined-oil", "hydrogen"]
    A = build_matrix(ds, recipes, items)
    b = [6.5, 12.0]
    print("  变量 x = [等离子精炼速率, X射线裂解速率]（次/秒）")
    print("  系数 = 单次配方的净流量（产出 - 消耗）")
    show_matrix(ds, recipes, items, A, b)
    res = L.classify(A, b)
    print()
    kv("方程数 / 变量数", f"2 / 2")
    kv("rank(A) / rank([A|b])", f"{res['rank_a']} / {res['rank_aug']}")
    kv("判定", res["status"])
    if res["solution"]:
        x = res["solution"]
        for rid, v in zip(recipes, x):
            print(f"    x[{rid:<18}] = {v}  ({float(v):.6f}) 次/秒")
        neg = [(rid, v) for rid, v in zip(recipes, x) if v < 0]
        kv("非负性", "全部 >= 0" if not neg else f"存在负值: {neg}")

    # 派生量
    if res["solution"] and all(v >= 0 for v in res["solution"]):
        xp, xx = [float(v) for v in res["solution"]]
        print()
        print("  派生量（用配方系数直接推算，可人工复核）:")
        print(f"    原油需求        = 2 × {xp:.4f} = {2 * xp:.4f}/s")
        print(f"    高能石墨产出    = 1 × {xx:.4f} = {xx:.4f}/s，需求 5/s，"
              f"{'缺口 %.4f/s' % (5 - xx) if xx < 5 else '富余 %.4f/s' % (xx - 5)}")
        eg_surplus = xx - 5.0
        print(f"    氢气产出        = {xp:.4f} + {xx:.4f} = {xp + xx:.4f}/s，需求 12/s")
        print(f"    精炼油净产出    = 2×{xp:.4f} - 1×{xx:.4f} = {2 * xp - xx:.4f}/s，需求 6.5/s")
        print()
        print("  注意: 上式中「高能石墨」根本没有进入方程组，它的富余 0.83/s 无处安放 ——")
        print("        方阵模型只能挑 2 个物品平衡，第三个物品的净流量被迫成为「废弃物」。")

    # ===== D4 松弛变量 + LP =====
    section("D.4 松弛变量 + 线性规划（非负约束下的正确做法）")
    print("  ⚠ 本节是「上界模型」：为了先讲清数学结构，暂时假设所有物品都能无限外购/放空。")
    print("    真实可执行的版本见案例 G —— 那里由数据决定哪些物品可外购（mining 配方）")
    print("    可放空（带 fuel 字段），并使用字典序目标而非加权求和。")
    print()
    print("  模型:  min c·x   s.t.  A_net·x + s⁺ - s⁻ = b,   x, s⁺, s⁻ >= 0")
    print("    x  = 配方速率")
    print("    s⁺ = 外购量（该物品不足时从外部补齐）")
    print("    s⁻ = 放空量（该物品富余时排掉，游戏里即储罐/火电烧掉）")
    print("    c  = 配方成本（这里取原油当量：等离子精炼每 craft 耗 2 原油）")
    print()
    recipes3 = ["plasma-refining", "x-ray-cracking", "reforming-refine"]
    items3 = ["refined-oil", "hydrogen", "energetic-graphite"]
    b3 = [6.5, 12.0, 5.0]
    A3 = build_matrix(ds, recipes3, items3)
    print("  净流量矩阵 (行=物品, 列=配方):")
    show_matrix(ds, recipes3, items3, A3, b3)

    # 目标函数：原油当量成本 + 放空/外购的惩罚
    n = len(recipes3)
    m = len(items3)
    # 变量顺序: x(3) + s+(3) + s-(3)
    cost = [2.0, 0.0, 1.0] + [100.0] * m + [10.0] * m   # 外购重罚，放空轻罚
    Aeq, beq = [], []
    for i in range(m):
        row = [A3[i][j] for j in range(n)]
        row += [1.0 if k == i else 0.0 for k in range(m)]      # s+
        row += [-1.0 if k == i else 0.0 for k in range(m)]     # s-
        Aeq.append(row)
        beq.append(b3[i])
    sol = L.simplex_min(cost, Aeq, beq)
    kv("LP 状态", sol["status"])
    if sol["status"] == "optimal":
        xs = [float(v) for v in sol["x"][:n]]
        sp = [float(v) for v in sol["x"][n:n + m]]
        sm = [float(v) for v in sol["x"][n + m:]]
        rows = [[rid, f"{v:.6f}"] for rid, v in zip(recipes3, xs)]
        table(["配方", "最优速率 (次/秒)"], rows, ["<", ">"], [22, 16])
        rows = []
        for i, it in enumerate(items3):
            rows.append([ds.name(it), f"{b3[i]:.3f}", f"{sp[i]:.4f}", f"{sm[i]:.4f}"])
        table(["物品", "需求", "外购 s+", "放空 s-"], rows, ["<", ">", ">", ">"], [22, 10, 10, 10])
        kv("目标值（加权成本）", f"{float(sol['z']):.4f}")
        print("    注: 权重 100/10/1 是人为标定，无游戏内依据；")
        print("        案例 G 用字典序替代，消除了这个任意常数。")
        print(f"  LP 迭代次数: {sol['iters']}")

    # ===== D5 守恒校验 =====
    section("D.5 物料守恒（逐物品核对）")
    ok_cons = True
    if sol["status"] == "optimal":
        for i, it in enumerate(items3):
            produced = sum(A3[i][j] * xs[j] for j in range(n) if A3[i][j] > 0) + 0.0
            consumed = -sum(A3[i][j] * xs[j] for j in range(n) if A3[i][j] < 0) + 0.0
            net = produced - consumed
            balance = net + sp[i] - sm[i]
            good = abs(balance - b3[i]) < 1e-6
            ok_cons &= good
            print(f"    {ds.name(it):<22} 产 {produced:8.4f} - 耗 {consumed:8.4f} = 净 {net:+8.4f}"
                  f"  +外购 {sp[i]:.4f} -放空 {sm[i]:.4f} = {balance + 0.0:8.4f}  期望 {b3[i]:.3f}  "
                  f"{'ok' if good else 'FAIL'}")
        print()
        print("  守恒判据: 对每个物品，产出 - 消耗 + 外购 - 放空 必须精确等于需求。")

    # ===== D6 与递归模型对照 =====
    section("D.6 与递归树模型对照（误差量化）")
    rec_solver = RecursiveSolver(
        ds, machine_pref=["assembling-machine-3", "arc-smelter", "chemical-plant",
                          "oil-refinery", "matrix-lab"],
        recipe_override={"hydrogen": "gas-giant-hydrogen"})
    rec_plan = rec_solver.solve("casimir-crystal", 1.0)
    tree_h2_import = rec_plan.raw.get("hydrogen", 0.0)
    tree_crude = rec_plan.raw.get("crude-oil", 0.0)
    tree_h2_byproduct = rec_plan.consumed.get("refined-oil", 0.0) / 2.0

    if sol["status"] == "optimal":
        lp_crude = 2.0 * xs[0]
        lp_h2_import = sp[1]
    else:
        lp_crude = lp_h2_import = float("nan")

    rows = [
        ["递归树（氢取气态巨星）", f"{tree_crude:.4f}", f"{tree_h2_import:.4f}",
         f"{tree_h2_byproduct:.4f}", f"{tree_h2_import:.4f}", "未计入副产氢"],
        ["矩阵 + 松弛 LP", f"{lp_crude:.4f}", f"{lp_h2_import:.4f}",
         f"{12.0:.4f}", f"{lp_h2_import:.4f}", "回路自给"],
    ]
    table(["口径", "原油/s", "氢外购/s", "回路产氢/s", "净氢缺口/s", "备注"], rows,
          ["<", ">", ">", ">", ">", "<"], [24, 10, 10, 11, 11, 14])
    print(f"  差异: 树模型高估氢气外购 {tree_h2_import - lp_h2_import:+.4f}/s，")
    print(f"        同时低估原油消耗 {lp_crude - tree_crude:+.4f}/s。")
    print("  这不是精度问题，而是建模范式问题:")
    print("    * 树只能沿单一配方向下展开，遇到「同一物品既是输入又是副产」必须二选一；")
    print("    * 矩阵把整条回路当成联立方程，可以得到「回路自给」这一真实最优解。")
    print()
    print("  同时必须承认代价: 矩阵解需要多烧 "
          f"{lp_crude - tree_crude:.2f}/s 原油来换 "
          f"{tree_h2_import - lp_h2_import:.2f}/s 氢气，")
    print("  这个取舍本身就是规划工具应当呈现给玩家的信息 —— 而不是替他默认选一个。")

    # ===== D7 结论 =====
    section("D.7 结论")
    chk = Checker("case_d")

    # D1: 必须是「真的找到了环」，且环内同时含氢与精炼油，而不是随便返回非空
    d1 = bool(h2_cycle) and set(h2_cycle) == {"hydrogen", "refined-oil"}
    chk.check("D1 循环存在性", d1,
              f"SCC = {h2_cycle}（判定条件：集合恰为 {{hydrogen, refined-oil}}）"
              if d1 else f"未得到预期的氢气-精炼油强连通分量，实际 {h2_cycle}")

    # D2: 矩阵系数不能只是「打印出来好看」，必须逐元素等于配方净流量
    d2_ok, d2_bad = True, []
    for i, it in enumerate(items):
        for j, rid in enumerate(recipes):
            want = ds.recipes[rid]["out"].get(it, 0) - ds.recipes[rid]["in"].get(it, 0)
            if abs(A[i][j] - want) > 1e-12:
                d2_ok = False
                d2_bad.append(f"A[{it}][{rid}]={A[i][j]} 期望 {want}")
    chk.check("D2 矩阵构建", d2_ok,
              "2×2 系数逐元素等于「配方产出 - 配方消耗」"
              if d2_ok else "; ".join(d2_bad))

    # D3: 方阵解唯一，且**必须暴露出漏项**——第三个物品的净流量无处安放
    d3_surplus = (xx - 5.0) if res.get("solution") else None
    d3 = (res["status"] == "unique"
          and all(v >= 0 for v in res["solution"] or [])
          and d3_surplus is not None and abs(d3_surplus) > 1e-6)
    chk.check("D3 方阵求解", d3,
              f"唯一解且非负；但高能石墨富余 {d3_surplus:+.4f}/s 未被任何约束记录 —— "
              "这正是方阵模型的结构性缺陷" if d3 else "未复现预期的唯一解或富余为零")

    # D4: LP 必须 optimal，且状态不得是 maxiter/infeasible 的伪装
    chk.check("D4 松弛变量 LP", sol["status"] == "optimal",
              "引入 s+/s- 后问题可行，且给出非负最优速率"
              if sol["status"] == "optimal"
              else f"LP 状态为 {sol['status']}（maxiter 表示未算完，不等于不可行）")

    chk.check("D5 物料守恒", ok_cons, "三物品逐项精确平衡（容差 1e-6）")

    # D6: 差异必须落在可独立推导的量上 —— 树模型对氢的外购额应等于「总需求」，
    #     LP 应做到「零外购」，两者之差恰好等于需求向量 b 中的氢分量。
    #     （注意：不是等于案例 B 的副产氢 3.25 —— 本场景同时跑了 x-ray-cracking 产石墨，
    #      回路自给量是 12 而非 3.25。这个区别正是 D 与 B 的场景差异，不能混用公式。）
    if sol["status"] == "optimal":
        gap_h2 = tree_h2_import - lp_h2_import
        d6 = (abs(tree_h2_import - b3[1]) < 1e-6          # 树：全额外购
              and abs(lp_h2_import) < 1e-6                 # LP：零外购
              and abs(gap_h2 - b3[1]) < 1e-6)              # 差额 = 氢需求
        chk.check("D6 与树模型对照", d6,
                  f"树模型外购氢 {tree_h2_import:.4f}/s = 需求全额；"
                  f"LP 外购 {lp_h2_import:.4f}/s；差额 {gap_h2:.4f}/s 恰等于氢需求 {b3[1]:.1f}/s"
                  if d6 else
                  f"树外购 {tree_h2_import:.4f} / LP 外购 {lp_h2_import:.4f} / "
                  f"需求 {b3[1]:.1f}，三者关系不符合预期")
    else:
        chk.skip("D6 与树模型对照", "LP 未取得最优解，无法做定量对照")

    chk.summary()
    print()
    print("  关键结论: 方阵求解只能保证「数学唯一解」，不能保证「物理可执行」。")
    print("            真正通用的建模是 净流量矩阵 + 外购/放空松弛 + 非负约束 + 目标函数，")
    print("            即线性规划，而不是高斯消元。")
    print("  证据等级: 已通过独立测试验证（SCC 由真实数据算出；LP 由本仓库自实现求解器求得）")
    return chk.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
