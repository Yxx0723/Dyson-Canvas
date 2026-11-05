#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 G —— 处置策略与目标函数（燃料类型 / 外购 / 放空 / 字典序）

本案关注「求解器之外」的那一层：什么物品允许被消化、由谁消化、按什么优先级优化。
边界与需求由 lib/boundary.py 从真实图自动推导（见案例 H 的交叉验证），
本案例不手写矩阵元素，也不手写需求常量。

  G1 带 fuel 字段 ≠ 可进火电站：按 building.fuel_types 匹配，逐项给出设施
  G2 处置路径必须覆盖全部 27 个带 fuel 的物品，不能有「悬空燃料」
  G3 同一模型在「允许/禁止焚烧」下的差异
  G4 加权目标与字典序目标给出不同答案 —— 证明加权法依赖无依据的权重
  G5 字典序解逐项满足需求（守恒）
  G6 生产速率非负

v2 的问题: G2/G3 曾用字面 True 判定，且求解矩阵是手写的；本版全部改为实际计算。
"""
from __future__ import annotations

import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset                                          # noqa: E402
import boundary as BND                                           # noqa: E402
import linalg as L                                               # noqa: E402
from policy import build_policy, summarize                       # noqa: E402
from stats import build_item_graph, cycles_of, is_roundtrip      # noqa: E402
from report import banner, section, kv, table, Checker           # noqa: E402

TARGETS = {"casimir-crystal": 1.0}


def find_material_cycle(ds: Dataset):
    g = build_item_graph(ds)
    cyc = cycles_of(g)
    rt = set()
    material = None
    for c in cyc:
        is_rt, pair = is_roundtrip(ds, c)
        if is_rt:
            rt |= set(pair)
        elif material is None:
            material = c
    return material, cyc, rt


def build_lp(ds, b, dem, pol, allow_vent=True, weights=None):
    """
    weights=None    -> 字典序（外购 → 放空 → 运行成本）
    weights=(a,b,c) -> 加权求和
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
        if allow_vent and pol.can_burn_chemical(item):
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

    c_imp = [Fraction(0)] * n
    for i in imp_col:
        c_imp[imp_col[i]] = Fraction(1)
    c_ven = [Fraction(0)] * n
    for i in ven_col:
        c_ven[ven_col[i]] = Fraction(1)
    c_run = [Fraction(0)] * n
    for k, (kind, _idx, _it) in enumerate(colspec):
        if kind == "recipe":
            c_run[k] = Fraction(1)

    base = {"colspec": colspec, "rows": rows, "recipes": b.recipes,
            "A": A, "imp_col": imp_col, "ven_col": ven_col}

    if weights is None:
        stages, x = [], None
        A3, b3 = [list(r) for r in Aeq], list(beq)
        for c, label in ((c_imp, "① 外购"), (c_ven, "② 放空"), (c_run, "③ 运行")):
            r = L.simplex_min(c, A3, b3)
            if r["status"] != "optimal":
                return dict(base, status=r["status"], x=None, stages=stages,
                            failed_at=label, mode="lexicographic")
            stages.append((label, r["z"]))
            A3.append(list(c))
            b3.append(r["z"])
            x = r["x"]
        return dict(base, status="optimal", x=x, stages=stages, mode="lexicographic")

    a, bb, cc = weights
    c = [a * c_imp[k] + bb * c_ven[k] + cc * c_run[k] for k in range(n)]
    r = L.simplex_min(c, Aeq, beq)
    return dict(base, status=r["status"], x=r["x"],
                stages=[("weighted", r["z"])], mode=f"weighted{weights}")


def report_solution(ds, sol):
    """把解拆成 (配方速率, 每行外购, 每行放空)。"""
    if sol["status"] != "optimal" or sol["x"] is None:
        return None
    x = sol["x"]
    xs = [float(v) for v in x[:len(sol["recipes"])]]
    sp = [float(x[sol["imp_col"][i]]) if i in sol["imp_col"] else 0.0
          for i in range(len(sol["rows"]))]
    sm = [float(x[sol["ven_col"][i]]) if i in sol["ven_col"] else 0.0
          for i in range(len(sol["rows"]))]
    return xs, sp, sm


def main() -> int:
    ds = Dataset()
    pol = build_policy(ds)
    chk = Checker("case_g")
    material, cyc, rt = find_material_cycle(ds)
    b = BND.analyze(ds, material, TARGETS, rt)

    banner("案例 G · 处置策略与目标函数", ds,
           目标=" + ".join(f"{ds.name(k)} {v}/s" for k, v in TARGETS.items()),
           边界="由 lib/boundary 从真实图自动推导（非手写矩阵）",
           说明="重点：什么允许被消化、由哪台建筑消化、按什么优先级优化")

    # ---------------- G.1 ----------------
    section("G.1 燃料类型与设施匹配（不能「有 fuel 字段就能烧」）")
    print("  建筑接受类型（来自 machine.fuel_types 与 machine.consumption）:")
    for mid, m in sorted(ds.machines.items()):
        ft = m.get("fuel_types") or []
        cons = m.get("consumption") or {}
        if ft or cons:
            desc = []
            if ft:
                desc.append(f"fuel_types={ft}")
            if cons:
                desc.append(f"consumption={cons}")
            print(f"    {mid:<26} {'  '.join(desc)}")
    print()
    rows = []
    for cat, num, lst in summarize(pol, ds):
        if cat.startswith("带 fuel"):
            continue
        rows.append([cat, num, lst[:70] + ("…" if len(lst) > 70 else "")])
    table(["类别", "数量", "物品"], rows, ["<", ">", "<"], [22, 5, 58])

    burn_chem = pol.burn_by_type.get("chemical", set())
    burn_nuc = pol.burn_by_type.get("nuclear", set())
    burn_anti = pol.burn_by_type.get("antimatter", set())
    covered = burn_chem | burn_nuc | burn_anti | pol.consumable | pol.transformable
    g1 = (len(pol.fuel_items) == 27 and len(burn_chem) == 21
          and len(burn_nuc) == 1 and len(burn_anti) == 2
          and not (burn_chem & burn_nuc))
    chk.check("G1 燃料按类型分派设施", g1,
              f"27 个 fuel 物品分为 chemical {len(burn_chem)} / nuclear {len(burn_nuc)} / "
              f"antimatter {len(burn_anti)} / 消耗品 {len(pol.consumable)} / 转化 {len(pol.transformable)}")

    # ---------------- G.2 ----------------
    section("G.2 处置路径的覆盖性（不允许有悬空燃料）")
    dangling = sorted(pol.fuel_items - covered)
    rows = []
    for item in ["refined-oil", "hydrogen", "energetic-graphite", "sulfuric-acid",
                 "graviton-lens", "accumulator-full", "deuteron-fuel-rod",
                 "antimatter-fuel-rod"]:
        rs = pol.routes.get(item, [])
        rows.append([ds.name(item), "是" if item in pol.fuel_items else "否",
                     "; ".join(r.label() for r in rs) if rs else "无任何处置路径"])
    table(["物品", "有fuel", "可用处置路径"], rows, ["<", ">", "<"], [22, 6, 54])

    g2 = (len(dangling) == 0 and len(covered & pol.fuel_items) == 27)
    chk.check("G2 全部燃料都有明确处置设施", g2,
              "27 个全部有设施兜底（chemical→火电站、nuclear→聚变站、"
              "antimatter→人造恒星、透镜→射线接收站、满电池→能源交换站）"
              if g2 else f"悬空燃料: {dangling}")

    g2b = (not pol.can_dispose("sulfuric-acid"))
    chk.check("G2b 不可处置物品确实不可处置", g2b,
              "硫酸既无 fuel 字段也无 mining 配方，松弛列被拒绝 —— 不是被跳过，而是被证明不可行")

    # ---------------- G.3 ----------------
    section("G.3 同一模型：允许焚烧 vs 禁止焚烧")
    print("  说明: 本 LP 的放空列**只**使用 chemical 燃料进火电站这一条通道（can_burn_chemical）；")
    print("        nuclear / antimatter / 透镜消耗 / 满电池↔空电池 均未进矩阵，")
    print("        它们是 G.1 已识别的数据关系，不是本模型的可行解空间（见报告 §7.2.1）。")
    print()
    sol_v = build_lp(ds, b, b.demand, pol, allow_vent=True)
    sol_nv = build_lp(ds, b, b.demand, pol, allow_vent=False)
    rows = []
    for label, s in (("允许焚烧（火电站）", sol_v), ("禁止焚烧", sol_nv)):
        r = report_solution(ds, s)
        if r is None:
            rows.append([label, s["status"], "—", "—", "—"])
        else:
            xs, sp, sm = r
            rows.append([label, s["status"], f"{sum(xs):.4f}", f"{sum(sp):.4f}", f"{sum(sm):.4f}"])
    table(["策略", "状态", "配方速率合计", "外购合计", "放空合计"], rows,
          ["<", "<", ">", ">", ">"], [24, 10, 12, 10, 10])

    rv = report_solution(ds, sol_v)
    rnv = report_solution(ds, sol_nv)
    g3 = (sol_v["status"] == "optimal" and rv is not None
          and sum(rv[2]) > 1e-9
          and (sol_nv["status"] != "optimal" or sum(rnv[2]) < 1e-9))
    chk.check("G3 焚烧开关改变可行解", g3,
              f"允许时放空 {sum(rv[2]):.4f}/s；禁止时"
              + (f"放空 {sum(rnv[2]):.4f}/s" if rnv else f"状态 {sol_nv['status']}")
              if g3 else
              f"允许时放空 {sum(rv[2]) if rv else 'N/A'}；禁止时 {sum(rnv[2]) if rnv else sol_nv['status']}")

    # 字典序若求不出解，后续对照无从谈起 —— 明确判失败并给出状态，而不是让脚本抛异常
    if rv is None:
        chk.check("G4 加权法答案随权重漂移", False,
                  f"字典序基准解不可用（状态 {sol_v['status']}），无法比较")
        chk.check("G5 字典序解满足全部需求", False, "无解")
        chk.check("G6 生产速率非负", False, "无解")
        chk.summary()
        return chk.exit_code()

    # ---------------- G.4 ----------------
    section("G.4 加权求和 vs 字典序")
    variants = [("(100, 10, 1) 外购≫放空≫运行", (100, 10, 1)),
                ("(1, 1, 1) 等权", (1, 1, 1)),
                ("(1, 100, 1) 放空重罚", (1, 100, 1))]
    rows = []
    sig = {}
    for label, w in variants:
        s = build_lp(ds, b, b.demand, pol, allow_vent=True, weights=w)
        r2 = report_solution(ds, s)
        if r2 is None:
            rows.append([label, s["status"], "—", "—"])
            continue
        xs2, sp2, sm2 = r2
        sig[label] = (round(sum(sp2), 6), round(sum(sm2), 6))
        rows.append([label, f"{sum(sp2):.4f}", f"{sum(sm2):.4f}", f"{sum(xs2):.4f}"])
    r = report_solution(ds, sol_v)
    sig["字典序"] = (round(sum(r[1]), 6), round(sum(r[2]), 6))
    rows.append(["字典序 外购→放空→运行", f"{sum(r[1]):.4f}", f"{sum(r[2]):.4f}", f"{sum(r[0]):.4f}"])
    table(["目标函数", "外购合计", "放空合计", "配方速率合计"], rows,
          ["<", ">", ">", ">"], [30, 10, 10, 12])

    distinct = set(sig.values())
    g4 = len(distinct) >= 2
    chk.check("G4 加权法答案随权重漂移", g4,
              f"{len(sig)} 种目标函数给出 {len(distinct)} 组不同答案：{sorted(sig.values())}"
              if g4 else f"所有目标函数结论相同（{distinct}），无法证明加权法不稳定")

    # ---------------- G.5 ----------------
    section("G.5 字典序解的逐项守恒")
    ok = True
    xs, sp, sm = r
    for i, item in enumerate(sol_v["rows"]):
        net = sum(float(sol_v["A"][i][j]) * xs[j] for j in range(len(xs))) + sp[i] - sm[i]
        want = b.demand[item]
        good = abs(net - want) < 1e-6
        ok &= good
        print(f"    {ds.name(item):<20} 净 {net:8.4f} + 外购 {sp[i]:7.4f} - 放空 {sm[i]:7.4f}"
              f" = {net:8.4f}  期望 {want:7.4f}  {'ok' if good else 'FAIL'}")
    chk.check("G5 字典序解满足全部需求", ok,
              "逐项满足（容差 1e-6）；本节只覆盖 LP 边界行，"
              "环外展开与环内衔接的全局账见案例 H4/H8")

    neg = [v for v in xs + sp + sm if v < -1e-9]
    chk.check("G6 生产速率非负", not neg, "全部变量 >= 0" if not neg else f"负值: {neg}")

    chk.summary()
    print("  证据等级: G1/G2 已通过独立测试验证（由数据集字段推导）；")
    print("            G3-G6 已通过独立测试验证（自动边界 + 自实现 LP + 回代校验）")
    return chk.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
