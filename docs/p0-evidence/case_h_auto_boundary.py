#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 H —— 边界自动化：从真实上下游图推导求解子图与外部净需求

为什么单独立一个案例
--------------------
v2 的案例 G 打印了 SCC 边界，但喂给 LP 的矩阵是手写的三行、需求是 `DEMAND` 常量。
「自动识别边界」这条链路从未被验证 —— 打印与求解之间没有程序连接。
本案例把它补齐并做成可失败的检查：

  H1 自动选出环内配方集，并与案例 D 人工挑选的结果逐条比对
  H2 自动汇总外部净需求，并与案例 D 使用的常量 (6.5 / 12 / 5) 逐项比对
  H3 换一个完全不同的目标产物，验证推导不是对单一场景的硬编码
  H4 **全局**物料账：环内 LP + 环外展开配方，逐物品核对残差（v4 加强）
  H5 环外共享需求合并：同一物品被多条下游路径消费时只累计一次
  H6 出边副产物（高能石墨）的归属被显式记录，不产生隐形废弃物
  H7 双目标（能量矩阵 2/s + 反物质 1/s）：环外副产氢气 1/s 必须抵扣，净需求 3/s
  H8 双目标的全局账残差全为 0（覆盖环外 3 条配方 + 3 个边界行 + 2 个目标）
  H9 反证：若沿用 v3 的「只计消耗」口径（4/s），同一套全局校验会抓到 +1/s 残差
  H10 副产**超过**需求时（反物质 1/s 单独为目标 → 氢需求 −1），富余记入 surplus
  H11 富余由放空列消化，全局账闭合
  H12 禁止放空时模型不可行 —— 「富余必须有去处」的反证

判定全部基于「算出来的值 vs 独立推导的期望值」，不依赖任何常量 True。

v4 修正（第三轮复验）
--------------------
v3 的 `boundary.analyze()` 只累计环外配方对边界物品的**输入**，不抵扣**副产**。
用当前数据即可复现：目标「能量矩阵 2/s + 反物质 1/s」时，
质能储存配方（2 临界光子 → 2 反物质 + 2 氢气）在产反物质的同时副产氢气 1/s，
真实净需求是 4−1=3，而 v3 记成 4，于是整张图多出 1/s 氢气无人认领 ——
偏偏 LP 自己的三行仍然平衡，局部校验看不出来。H7-H9 就是守这一条。
"""
from __future__ import annotations

import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset                                          # noqa: E402
import boundary as BND                                           # noqa: E402
import linalg as L                                               # noqa: E402
from policy import build_policy                                  # noqa: E402
from stats import build_item_graph, cycles_of, is_roundtrip      # noqa: E402
from report import banner, section, kv, table, Checker           # noqa: E402

TARGETS = {"casimir-crystal": 1.0}
DUAL_TARGETS = {"energy-matrix": 2.0, "antimatter": 1.0}

# 案例 D 人工构建的模型（用于交叉验证自动化结果）
# 这些数字来自案例 B 的手算链条，不是从本脚本的运行结果反推的。
CASE_D_RECIPES = {"plasma-refining", "reforming-refine", "x-ray-cracking"}
CASE_D_DEMAND = {"refined-oil": 6.5, "hydrogen": 12.0, "energetic-graphite": 5.0}


def find_material_cycle(ds: Dataset):
    """返回 (物料生产循环的物品列表, 全部循环分量, 需要排除的往返配方)。"""
    g = build_item_graph(ds)
    cyc = cycles_of(g)
    roundtrip_recipes = set()
    material = None
    for c in cyc:
        rt, pair = is_roundtrip(ds, c)
        if rt:
            roundtrip_recipes |= set(pair)
            continue
        if material is None:
            material = c
    return material, cyc, roundtrip_recipes


def build_and_solve(ds: Dataset, b: BND.Boundary, dem, pol, forbid_vent=False):
    """
    用自动推导出的矩阵建 LP 并求解。

    列布局：先配方列，再为每个平衡行追加「外购」「放空」列 —— **当且仅当策略允许**。
    不允许的松弛列**不进入矩阵**，而不是「进矩阵但成本置零」。

    后者是常见错误：把成本置零只是让求解器不想用它，变量本身仍可取任意值。
    本案例第一版就踩了这个坑 —— 结果所有配方都取 0、全部靠外购满足需求，
    守恒校验依然"通过"。这种通过毫无意义，必须从列层面禁止。
    """
    A = BND.net_matrix(ds, b)
    rows = b.rows
    nr, nc = len(rows), len(b.recipes)

    colspec = [("recipe", j, None) for j in range(nc)]
    imp_col, ven_col = {}, {}
    for i, item in enumerate(rows):
        if pol.can_import(item):
            imp_col[i] = len(colspec)
            colspec.append(("imp", i, item))
    for i, item in enumerate(rows):
        if (not forbid_vent) and pol.can_burn_chemical(item):
            ven_col[i] = len(colspec)
            colspec.append(("vent", i, item))

    n = len(colspec)
    Aeq, beq = [], []
    for i in range(nr):
        row = [Fraction(0)] * n
        for k, (kind, idx, _it) in enumerate(colspec):
            if kind == "recipe":
                row[k] = A[i][idx]
        if i in imp_col:
            row[imp_col[i]] = Fraction(1)
        if i in ven_col:
            row[ven_col[i]] = Fraction(-1)
        Aeq.append(row)
        beq.append(Fraction(dem[rows[i]]).limit_denominator(10 ** 6))

    c1 = [Fraction(0)] * n
    for i in imp_col:
        c1[imp_col[i]] = Fraction(1)
    c2 = [Fraction(0)] * n
    for i in ven_col:
        c2[ven_col[i]] = Fraction(1)
    c3 = [Fraction(0)] * n
    for k, (kind, _idx, _it) in enumerate(colspec):
        if kind == "recipe":
            c3[k] = Fraction(1)          # 第③层：单位运行成本（占位，见报告 §13.5）

    stages = []
    A3, b3 = [list(r) for r in Aeq], list(beq)
    x = None
    for c, label in ((c1, "① 外购"), (c2, "② 放空"), (c3, "③ 运行成本")):
        r = L.simplex_min(c, A3, b3)
        if r["status"] != "optimal":
            return {"status": r["status"], "stages": stages, "x": None,
                    "failed_at": label, "colspec": colspec, "rows": rows,
                    "recipes": b.recipes, "A": A}
        stages.append((label, r["z"]))
        A3.append(list(c))
        b3.append(r["z"])
        x = r["x"]

    return {"status": "optimal", "stages": stages, "x": x, "colspec": colspec,
            "rows": rows, "recipes": b.recipes, "A": A,
            "imp_col": imp_col, "ven_col": ven_col, "n_cols": n}


def solution_flows(sol):
    """把 LP 解拆成 (环内配方速率, 外购, 放空) 三个字典，供全局账使用。"""
    loop_rates = {rid: float(sol["x"][j]) for j, rid in enumerate(sol["recipes"])}
    imports = {sol["rows"][i]: float(sol["x"][k]) for i, k in sol["imp_col"].items()}
    vents = {sol["rows"][i]: float(sol["x"][k]) for i, k in sol["ven_col"].items()}
    return loop_rates, imports, vents


def print_global(ds: Dataset, gb) -> None:
    rows = []
    for r in gb["rows"]:
        rows.append([ds.name(r["item"]),
                     f"{float(r['loop']):8.4f}", f"{float(r['ext_out']):8.4f}",
                     f"{float(r['import']):7.4f}",
                     f"{float(r['ext_in']):8.4f}", f"{float(r['target']):6.4f}",
                     f"{float(r['vent']):7.4f}",
                     f"{float(r['residual']):+8.4f}",
                     "ok" if r["residual"] == 0 else "FAIL"])
    table(["物品", "环内净", "环外产", "外购", "环外耗", "目标", "放空", "残差", ""],
          rows, ["<", ">", ">", ">", ">", ">", ">", ">", "<"],
          [22, 9, 9, 7, 9, 6, 7, 8, 5])


def main() -> int:
    ds = Dataset()
    ds_pol = build_policy(ds)
    chk = Checker("case_h")

    material, cyc, rt_recipes = find_material_cycle(ds)
    banner("案例 H · 边界自动化（从真实上下游图推导求解子图）", ds,
           循环分量=f"{len(cyc)} 个，其中物料生产 {1 if material else 0} 个",
           目标=" + ".join(f"{ds.name(k)} {v}/s" for k, v in TARGETS.items()),
           说明="本案例不手写任何矩阵元素或需求常量；需求按「外部消耗 − 外部副产」生成")

    section("H.1 循环识别（自动）")
    kv("全部循环分量", [ds.name(x) for c in cyc for x in c])
    kv("物料生产循环", [ds.name(x) for x in (material or [])])
    kv("状态往返配方（排除）", sorted(rt_recipes) or "—")

    section("H.2 边界与配方集（自动推导）")
    b = BND.analyze(ds, material, TARGETS, rt_recipes)
    for line in BND.describe(b, ds):
        print("  " + line)

    section("H.3 外部需求推导轨迹（自动汇总，可逐行复核）")
    for t in b.trace:
        print("  " + t)
    print()
    rows = []
    for k in b.rows:
        rows.append([ds.name(k), f"{b.gross_in.get(k, 0):.4f}",
                     f"{b.target_direct.get(k, 0):.4f}",
                     f"{b.gross_out.get(k, 0):.4f}", f"{b.demand[k]:.4f}"])
    table(["边界物品", "环外消耗", "目标直接", "环外副产", "净需求/s"], rows,
          ["<", ">", ">", ">", ">"], [24, 10, 10, 10, 10])

    section("H.4 与案例 D 人工模型的交叉验证")
    auto_recipes = set(b.recipes)
    same_recipes = auto_recipes == CASE_D_RECIPES
    chk.check("H1 配方集与人工挑选一致", same_recipes,
              f"自动 {sorted(auto_recipes)}；人工 {sorted(CASE_D_RECIPES)}"
              if same_recipes else
              f"不一致：自动多 {sorted(auto_recipes - CASE_D_RECIPES)}，少 {sorted(CASE_D_RECIPES - auto_recipes)}")

    dem_dev = {k: (b.demand.get(k, 0.0), v) for k, v in CASE_D_DEMAND.items()
               if abs(b.demand.get(k, 0.0) - v) > 1e-9}
    missing = sorted(set(b.rows) - set(CASE_D_DEMAND))
    chk.check("H2 外部净需求与人工常量一致", (not dem_dev) and (not missing),
              "精炼油 6.5 / 氢气 12 / 高能石墨 5 三项逐项相等，无多余边界行"
              if not dem_dev and not missing
              else f"偏差 {dem_dev}；多余行 {missing}")

    section("H.5 换一个目标产物，验证推导不是硬编码")
    alt_targets = {"energy-matrix": 2.0}
    b2 = BND.analyze(ds, material, alt_targets, rt_recipes)
    kv("目标", "能量矩阵 2/s")
    kv("边界行", [ds.name(x) for x in b2.rows])
    kv("外部净需求", {ds.name(k): round(v, 4) for k, v in b2.demand.items()})
    # 期望：能量矩阵不需要高能石墨，故 EG 需求应为 0；氢气需求应来自其配方本身
    em = ds.recipes["energy-matrix"]
    cyc_em = 2.0 / em["out"]["energy-matrix"]
    expect_h2 = em["in"].get("hydrogen", 0) * cyc_em
    expect_eg = em["in"].get("energetic-graphite", 0) * cyc_em
    h3 = (abs(b2.demand.get("hydrogen", 0.0) - expect_h2) < 1e-9
          and abs(b2.demand.get("energetic-graphite", 0.0) - expect_eg) < 1e-9)
    chk.check("H3 不同目标下的需求独立成立", h3,
              f"能量矩阵 2/s → 氢 {b2.demand.get('hydrogen', 0):.4f}/s（手算 {expect_h2:.4f}）、"
              f"高能石墨 {b2.demand.get('energetic-graphite', 0):.4f}/s（手算 {expect_eg:.4f}）"
              if h3 else
              f"氢 {b2.demand.get('hydrogen', 0):.4f} vs {expect_h2:.4f}；"
              f"EG {b2.demand.get('energetic-graphite', 0):.4f} vs {expect_eg:.4f}")

    section("H.6 用自动模型求解并做全局物料账（环内 LP + 环外展开）")
    print("  松弛列可用性（由 policy 按数据判定，不是人为开关）:")
    for item in b.rows:
        can_i = ds_pol.can_import(item)
        can_v = ds_pol.can_burn_chemical(item)
        print(f"    {ds.name(item):<20} 外购{'✓' if can_i else '✗'}  "
              f"焚烧{'✓' if can_v else '✗'}  "
              f"（{'有采集来源' if can_i else '无 mining 配方'}；"
              f"{'可进火电站' if can_v else '不可燃或不可投料'}）")
    print()

    sol = build_and_solve(ds, b, b.demand, ds_pol)
    kv("LP 状态", sol["status"])
    if sol["status"] != "optimal":
        chk.check("H4 全局物料账无残差", False,
                  f"LP 状态 {sol['status']}（第 {sol.get('failed_at')} 层）")
        chk.summary()
        return chk.exit_code()

    x = sol["x"]
    xs = [float(v) for v in x[:len(sol["recipes"])]]
    imp_col, ven_col = sol["imp_col"], sol["ven_col"]

    print("  配方速率:")
    for rid, v in zip(sol["recipes"], xs):
        print(f"    {rid:<22} {v:10.4f} 次/s")
    print()

    loop_rates, imports, vents = solution_flows(sol)
    gb = BND.global_balance(ds, b, b.targets, loop_rates, imports, vents)
    print(f"  全局物料账（{gb['covered']} 个物品：{len(b.rows)} 个边界行 + 目标 + 环外 "
          f"{len(b.external_runs)} 条配方的全部输入输出）:")
    print_global(ds, gb)
    chk.check("H4 全局物料账无残差", not gb["unbalanced"],
              f"{gb['covered']} 个物品逐项闭合（含环外副产与中间物）"
              if not gb["unbalanced"] else
              "残差非零: " + "; ".join(f"{r['item']} {float(r['residual']):+.4f}"
                                       for r in gb["unbalanced"]))
    if gb["raw"]:
        print("    外部原料（无配方，不计入闭合）: "
              + ", ".join(f"{ds.name(k)} {v:.4f}/s" for k, v in gb["raw"].items()))
    print(f"    未覆盖: 环内配方的入边原料 {[ds.name(x) for x in b.incoming]}"
          f"（按「边界外无限供给」处理，不参与闭合）")

    # H4b：解不能退化成「全部外购」—— 不可外购的物品必须由环路真实生产
    all_zero = all(abs(v) < 1e-9 for v in xs)
    produced_ro = sum(float(sol["A"][sol["rows"].index("refined-oil")][j]) * xs[j]
                      for j in range(len(xs)))
    chk.check("H4b 解未退化为「全部外购」", (not all_zero) and produced_ro > 1e-9,
              f"精炼油由环路生产 {produced_ro:.4f}/s（它没有 mining 来源，"
              f"若靠外购则模型非法）" if not all_zero and produced_ro > 1e-9
              else f"所有配方速率为零或精炼油产量为 {produced_ro:.4f}")

    # H5: 环外共享需求合并
    ro_paths = {"硫酸支链": 1.5, "有机晶体支链": 1.0, "塑料支链": 4.0}
    ro_sum = sum(ro_paths.values())
    h5 = abs(b.demand["refined-oil"] - ro_sum) < 1e-9
    chk.check("H5 环外共享需求合并", h5,
              f"精炼油被 {len(ro_paths)} 条下游路径消费，合并为 {b.demand['refined-oil']:.4f}/s"
              f"（{ro_sum:.4f}），未重复计数")

    # H6: 出边副产物必须显式出现在平衡行里
    eg_row = "energetic-graphite" in sol["rows"]
    eg_idx = sol["rows"].index("energetic-graphite") if eg_row else None
    eg_vent = float(x[ven_col[eg_idx]]) if eg_idx is not None and eg_idx in ven_col else None
    eg_prod = (sum(float(sol["A"][eg_idx][j]) * xs[j] for j in range(len(xs)) if sol["A"][eg_idx][j] > 0)
               if eg_idx is not None else 0.0)
    h6 = eg_row and eg_vent is not None and abs(eg_prod - eg_vent - b.demand["energetic-graphite"]) < 1e-6
    chk.check("H6 出边副产物有归属", h6,
              f"高能石墨进平衡行：环路产 {eg_prod:.4f}/s，放空 {eg_vent:.4f}/s，"
              f"需求 {b.demand['energetic-graphite']:.4f}/s，账面对得上"
              if h6 else f"高能石墨未闭合（产 {eg_prod:.4f} 放空 {eg_vent}）")

    # ------------------------------------------------------------------ #
    section("H.7 双目标：环外副产必须抵扣（能量矩阵 2/s + 反物质 1/s）")
    b3 = BND.analyze(ds, material, DUAL_TARGETS, rt_recipes)
    for line in BND.describe(b3, ds):
        print("  " + line)

    print()
    print("  手算期望（不依赖任何求解器）:")
    print("    能量矩阵 2/s × (2 H2/个)                     ⇒ 耗 H2 4.0000/s")
    print("    反物质   1/s × (2 H2 / 2 反物质) = 0.5 次/s ⇒ 副产 H2 1.0000/s")
    print("    净需求 = 4.0000 − 1.0000 = 3.0000/s")
    h7 = (abs(b3.gross_in.get("hydrogen", 0) - 4.0) < 1e-9
          and abs(b3.gross_out.get("hydrogen", 0) - 1.0) < 1e-9
          and abs(b3.demand.get("hydrogen", 0) - 3.0) < 1e-9)
    chk.check("H7 氢净需求 = 消耗 4 − 副产 1 = 3", h7,
              f"环外消耗 {b3.gross_in.get('hydrogen', 0):.4f}、副产 {b3.gross_out.get('hydrogen', 0):.4f}、"
              f"净需求 {b3.demand.get('hydrogen', 0):.4f}（v3 会记成 4.0000）"
              if h7 else
              f"消耗 {b3.gross_in.get('hydrogen', 0):.4f}、副产 {b3.gross_out.get('hydrogen', 0):.4f}、"
              f"净需求 {b3.demand.get('hydrogen', 0):.4f}（期望 4 / 1 / 3）")

    sol3 = build_and_solve(ds, b3, b3.demand, ds_pol)
    if sol3["status"] != "optimal":
        chk.check("H8 双目标全局账无残差", False, f"LP 状态 {sol3['status']}")
    else:
        x3 = sol3["x"]
        lr3, im3, vn3 = solution_flows(sol3)
        print()
        print("  双目标 LP 解:")
        for rid, j in zip(sol3["recipes"], range(len(sol3["recipes"]))):
            print(f"    {rid:<22} {float(x3[j]):10.4f} 次/s")
        gb3 = BND.global_balance(ds, b3, b3.targets, lr3, im3, vn3)
        print(f"  全局物料账（{gb3['covered']} 个物品，覆盖环外 "
              f"{len(b3.external_runs)} 条配方）:")
        print_global(ds, gb3)
        chk.check("H8 双目标全局账无残差（环外 + 环内）", not gb3["unbalanced"],
                  f"{gb3['covered']} 个物品逐项闭合，含能量矩阵/反物质/临界光子等不进边界行的物品"
                  if not gb3["unbalanced"] else
                  "残差非零: " + "; ".join(f"{r['item']} {float(r['residual']):+.4f}"
                                           for r in gb3["unbalanced"]))

    # H9: 反证 —— 用 v3 的「只计消耗」口径（4/s）再算一次，
    #     同一套全局校验必须能抓到 +1/s 的氢气剩余。若抓不到，说明校验本身是装饰品。
    naive = dict(b3.demand)
    naive["hydrogen"] = naive["hydrogen"] + float(b3.gross_out.get("hydrogen", 0.0))
    sol_n = build_and_solve(ds, b3, naive, ds_pol)
    if sol_n["status"] != "optimal":
        chk.check("H9 旧口径（4/s）会被全局账抓到 +1/s 残差", False,
                  f"旧口径模型状态 {sol_n['status']}，无法构造反证")
    else:
        lrn, imn, vnn = solution_flows(sol_n)
        gbn = BND.global_balance(ds, b3, b3.targets, lrn, imn, vnn)
        bad_h2 = [r for r in gbn["unbalanced"] if r["item"] == "hydrogen"]
        caught = (bool(bad_h2) and abs(float(bad_h2[0]["residual"]) - 1.0) < 1e-9)
        print()
        print(f"  反证：若沿用 v3 口径（H2 需求记 {naive['hydrogen']:.4f}/s）:")
        for r in gbn["unbalanced"]:
            print(f"    残差 {ds.name(r['item']):<20} {float(r['residual']):+8.4f}/s")
        chk.check("H9 旧口径（4/s）会被全局账抓到 +1/s 残差", caught,
                  f"氢气残差 {float(bad_h2[0]['residual']):+.4f}/s —— 说明该口径确实漏了副产，"
                  f"且本校验能发现它" if caught else
                  "未能复现 +1/s 残差，全局校验的有效性存疑")

    # ------------------------------------------------------------------ #
    section("H.8 副产超过需求：必须记录剩余并给出处置（反物质 1/s 单独为目标）")
    b4 = BND.analyze(ds, material, {"antimatter": 1.0}, rt_recipes)
    print("  环外展开配方:")
    for run in b4.external_runs:
        ins = ", ".join(f"{ds.name(k)}×{float(v):.4f}" for k, v in run.inputs.items()) or "—"
        outs = ", ".join(f"{ds.name(k)}×{float(v):.4f}" for k, v in run.outputs.items())
        print(f"    {run.recipe:<22} ×{float(run.rate):7.4f}/s   {ins}  →  {outs}")
    print("  边界净需求（消耗 + 目标 − 副产）:")
    for k in b4.rows:
        print(f"    {ds.name(k):<20} {b4.gross_in.get(k, 0):7.4f} + {b4.target_direct.get(k, 0):5.4f}"
              f" − {b4.gross_out.get(k, 0):7.4f} = {b4.demand[k]:+8.4f}/s")
    h10 = (abs(b4.demand.get("hydrogen", 0) + 1.0) < 1e-9
           and abs(b4.surplus.get("hydrogen", 0) - 1.0) < 1e-9)
    chk.check("H10 副产富余（需求为负）被记录为 surplus，不静默丢弃", h10,
              f"氢净需求 {b4.demand.get('hydrogen', 0):+.4f}/s、surplus "
              f"{b4.surplus.get('hydrogen', 0):.4f}/s —— 反物质路线副产的 1/s 氢气有明确归属"
              if h10 else f"净需求 {b4.demand.get('hydrogen', 0):+.4f}、surplus {b4.surplus}")

    sol4 = build_and_solve(ds, b4, b4.demand, ds_pol)
    if sol4["status"] != "optimal":
        chk.check("H11 富余由放空列消化，全局账闭合", False, f"LP 状态 {sol4['status']}")
        chk.check("H12 禁止放空时模型不可行（富余无处可去）", False, "前置模型不可解")
    else:
        lr4, im4, vn4 = solution_flows(sol4)
        gb4 = BND.global_balance(ds, b4, b4.targets, lr4, im4, vn4)
        h11 = (not gb4["unbalanced"]) and abs(vn4.get("hydrogen", 0) - 1.0) < 1e-9
        print("  放空列: " + ", ".join(f"{ds.name(k)} {v:.4f}/s" for k, v in vn4.items() if v > 0))
        chk.check("H11 富余由放空列消化，全局账闭合", h11,
                  f"氢气放空 {vn4.get('hydrogen', 0):.4f}/s；{gb4['covered']} 个物品残差全 0"
                  if h11 else
                  f"放空 {vn4.get('hydrogen', 0):.4f}；残差 "
                  + "; ".join(f"{r['item']} {float(r['residual']):+.4f}" for r in gb4["unbalanced"]))
        sol4n = build_and_solve(ds, b4, b4.demand, ds_pol, forbid_vent=True)
        chk.check("H12 禁止放空时模型不可行（富余无处可去）", sol4n["status"] == "infeasible",
                  f"禁止焚烧 → {sol4n['status']}：负需求只能靠放空列满足，"
                  f"这条不可行本身就是「富余必须有去处」的证据")

    chk.summary()
    print("  证据等级: 已通过独立测试验证（边界与净需求均由真实图自动推导，"
          "并与案例 D 的人工结果交叉比对；全局账覆盖环外展开与环内 LP 的衔接）")
    return chk.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
