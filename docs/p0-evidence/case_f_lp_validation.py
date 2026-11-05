#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 F —— 自研单纯形与独立基准的对照验证

为什么需要这一条:
  Python 原型跑通不等于 TypeScript 正式实现可信。在把「自研 LP」写进技术选型之前，
  必须先把同一个模型交给**算法上不同源**的基准求解并逐项比对：
    * 有界模型 → 顶点穷举（组合枚举 + 精确高斯消元，不做枢轴）
    * 不可行   → 增广矩阵 RREF 一致性 / 基可行解存在性
    * 无界     → 显式射线证书（x0 可行 + A·d=0 + d>=0 + c·d<0），逐项代数验证
  GLPK 5.0 是额外的第三方佐证，不是通过的必要条件。

覆盖六类场景（P0 审核要求）:
  F1 可行且有界唯一最优
  F2 不可行
  F3 无界
  F4 退化（Beale 循环示例，专测主元规则）
  F5 多解（无穷多最优，只比目标值）
  F6 实际 DSP 生产场景 ×3（氢气回路 / 多配方择优 / 无松弛不可行）

v4 修正（针对第三轮复验）
------------------------
1. `has_negative_ray()` 的判据在等式约束下不成立：A·d <= 0 不能替代 A·d = 0。
   反例 `min -x, -x = -1` 有唯一解 x=1，却被旧判据报成无界。
   现在无界必须由 `verify_ray_certificate()` 逐项验证 (x0, d) 六条条件（F12-F15）。
2. `enumerate_optimum()` 按原始行数选列，重复约束会让它返回 `no_vertices`——
   一个可行模型因此被判成不可行。现在先 RREF 查一致性、再按**有效行秩**枚举（F16-F19）。

判定规则:
  * 状态一致（optimal / infeasible / unbounded）
  * 目标值一致（|Δ| <= 1e-6）
  * **独立可行性校验**：把解代回原模型验等式与非负，不依赖任何求解器
  * 独立基准按模型类型选择，并在日志中逐条标注用的是哪一种
"""
from __future__ import annotations

import os
import sys
from fractions import Fraction
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset                                        # noqa: E402
import linalg                                                  # noqa: E402
from lpio import LPProblem, solve_self, solve_glpk, find_glpsol  # noqa: E402
from report import banner, section, kv, table, Checker         # noqa: E402


def build_problems(ds: Dataset):
    P = []

    # ---------------- F1 可行且有界 ----------------
    P.append(LPProblem(
        name="F1 可行·唯一最优",
        note="max 3x+2y, x+y<=4, x+3y<=6 （松弛后标准形，min -3x-2y）",
        c=[-3, -2, 0, 0],
        A=[[1, 1, 1, 0], [1, 3, 0, 1]],
        b=[4, 6],
        col_names=["x", "y", "s1", "s2"],
        row_names=["cap1", "cap2"],
    ))

    # ---------------- F2 不可行 ----------------
    P.append(LPProblem(
        name="F2 不可行",
        note="x+y=1 且 x+y=2，两约束矛盾",
        c=[1, 1],
        A=[[1, 1], [1, 1]],
        b=[1, 2],
        col_names=["x", "y"],
        row_names=["r1", "r2"],
    ))

    # ---------------- F3 无界 ----------------
    # v4: 证书必须显式。旧的「列 y 系数 <=0 且 c_y<0」是错的：
    #     A·e_y = -1 ≠ 0，沿 e_y 走一步就破坏 x-y=0。
    #     正确方向是 d=(1,1)：A·d = 0、d>=0、c·d = -2 < 0。
    P.append(LPProblem(
        name="F3 无界",
        note="min -x-y, x-y=0，x,y>=0 ⇒ 证书 x0=(0,0)、d=(1,1)",
        c=[-1, -1],
        A=[[1, -1]],
        b=[0],
        col_names=["x", "y"],
        row_names=["balance"],
    ))

    # ---------------- F4 退化（Beale） ----------------
    # 经典循环示例，专测主元规则；两种求解器都必须终止并给出同一最优值
    P.append(LPProblem(
        name="F4 退化·Beale 循环示例",
        note="max 0.75x1-150x2+0.02x3-6x4，三约束；朴素规则会循环震荡",
        c=[-0.75, 150, -0.02, 6, 0, 0, 0],
        A=[[0.25, -60, -0.04, 9, 1, 0, 0],
           [0.5, -90, -0.02, 3, 0, 1, 0],
           [0, 0, 1, 0, 0, 0, 1]],
        b=[0, 0, 1],
        col_names=["x1", "x2", "x3", "x4", "s1", "s2", "s3"],
        row_names=["r1", "r2", "r3"],
    ))

    # ---------------- F5 多解 ----------------
    P.append(LPProblem(
        name="F5 多解·无穷多最优",
        note="min 0，x+y=1；目标函数与可行方向正交 ⇒ 整条线段都是最优",
        c=[0, 0],
        A=[[1, 1]],
        b=[1],
        col_names=["x", "y"],
        row_names=["sum"],
    ))

    # ---------------- F6a 实际 DSP：氢气回路 + 松弛 ----------------
    # 真实配方系数（plasma-refining / x-ray-cracking / reforming-refine）
    # 变量: p, x, rf, sp(RO), sm(RO), sp(H2), sm(H2), sp(EG), sm(EG)
    P.append(LPProblem(
        name="F6a DSP·氢气回路（含松弛）",
        note="min 2p+rf，满足 RO 6.5 / H2 12 / EG 5（真实配方系数）",
        c=[2, 0, 1, 100, 10, 100, 10, 100, 10],
        A=[[2, -1, 1, 1, -1, 0, 0, 0, 0],
           [1, 1, -1, 0, 0, 1, -1, 0, 0],
           [0, 1, 0, 0, 0, 0, 0, 1, -1]],
        b=[6.5, 12.0, 5.0],
        col_names=["plasma", "xray", "reform", "ro_in", "ro_out",
                   "h2_in", "h2_out", "eg_in", "eg_out"],
        row_names=["refined_oil", "hydrogen", "energetic_graphite"],
    ))

    # ---------------- F6b 实际 DSP：多配方择优 ----------------
    # 石墨烯 3/s，同时需要氢 1.5/s。
    # 常规 graphene: 2 graphene/次，不产氢；原矿当量成本 7.5（煤6 + 原油1.5）
    # 高级 graphene-advanced: 2 graphene/次 + 1 氢/次；原矿当量成本 2.0（可燃冰2）
    # 权重为测试夹具，不是产品决策（见报告 13.x）
    P.append(LPProblem(
        name="F6b DSP·多配方择优",
        note="石墨烯 3/s + 氢 1.5/s；常规路线 7.5/次、高级路线 2.0/次",
        c=[7.5, 2.0, 0, 100],
        A=[[2, 2, -1, 0],
           [0, 1, 0, 1]],
        b=[3.0, 1.5],
        col_names=["graphene", "graphene_adv", "g_surplus", "h2_import"],
        row_names=["graphene_bal", "hydrogen_bal"],
    ))

    # ---------------- F6c 实际 DSP：无松弛 ⇒ 不可行 ----------------
    # 同 F6a 的约束，但不给外购/放空变量：3 个等式 2 个未知数
    P.append(LPProblem(
        name="F6c DSP·无松弛不可行",
        note="同上三约束，但不允许外购/放空 ⇒ 方程组超定且矛盾",
        c=[2, 0, 1],
        A=[[2, -1, 1],
           [1, 1, -1],
           [0, 1, 0]],
        b=[6.5, 12.0, 5.0],
        col_names=["plasma", "xray", "reform"],
        row_names=["refined_oil", "hydrogen", "energetic_graphite"],
    ))

    return P


# --------------------------------------------------------------------------- #
# 独立基准（ORACLE）
# --------------------------------------------------------------------------- #
# 每个模型都给出「预期状态 + 预期最优值」，来源各自独立于被测求解器：
#   F1  手算：max 3x+2y 在 (4,0) 取 12，故 min 为 -12
#   F2  手算：x+y=1 与 x+y=2 矛盾
#   F3  手算：沿 x=y 可无限降低。**证书显式给出**：x0=(0,0)、d=(1,1)，
#       A·x0=b、x0>=0、A·d=0、d>=0、c·d=-2<0 六条全成立（见 F13）。
#       v3 用「列 y 的系数 <=0 且成本 <0」当证书是错推理：A·e_y=-1≠0。
#   F4  Beale 退化例：由**顶点穷举**（与单纯形不同源）求得 -1/20
#   F5  手算：目标函数恒为 0，任一可行点都是最优 ⇒ 多解
#   F6a 由顶点穷举求得 62/3；另可用配方系数手算复核（见 D.4）
#   F6b 手算：全走高级路线，1.5 次产出 3 石墨 + 1.5 氢，成本 2×1.5=3
#   F6c 手算：3 等式 2 未知且右端不匹配，无解
#
# 关键：**判定以 ORACLE 为准，不以「两边是否一样」为准**。
# 两个实现同时错的时候，「一致」是没意义的。
ORACLE = {
    "F1 可行·唯一最优":        {"status": "optimal",    "z": Fraction(-12), "unique": True},
    "F2 不可行":              {"status": "infeasible", "z": None,           "unique": None},
    "F3 无界":                {"status": "unbounded",  "z": None,           "unique": None},
    "F4 退化·Beale 循环示例":   {"status": "optimal",    "z": Fraction(-1, 20), "unique": False},
    "F5 多解·无穷多最优":       {"status": "optimal",    "z": Fraction(0),  "unique": False},
    "F6a DSP·氢气回路（含松弛）": {"status": "optimal",    "z": Fraction(62, 3), "unique": False},
    "F6b DSP·多配方择优":      {"status": "optimal",    "z": Fraction(3),   "unique": False},
    "F6c DSP·无松弛不可行":     {"status": "infeasible", "z": None,           "unique": None},
}

# 无界模型的显式见证：人工给出，由程序逐项代数验证（不是从求解器里抄回来的）
RAY_CERTS = {
    "F3 无界": {"x0": [0, 0], "d": [1, 1]},
}


def status_matches(actual: str, expect: str) -> bool:
    return actual == expect


def objective(p: LPProblem, x) -> Optional[float]:
    if x is None:
        return None
    return sum(float(p.c[j]) * x[j] for j in range(len(p.c)))


def verify_feasible(p: LPProblem, x, tol: float = 1e-6):
    """把解代回原模型：等式残差 + 非负性。不依赖任何求解器。"""
    if x is None:
        return None, []
    bad = []
    for i, row in enumerate(p.A):
        lhs = sum(float(row[j]) * x[j] for j in range(len(x)))
        if abs(lhs - float(p.b[i])) > tol:
            bad.append(f"行{i} 残差 {lhs - float(p.b[i]):+.3e}")
    for j, v in enumerate(x):
        if v < -tol:
            bad.append(f"x{j} = {v:.3e} < 0")
    return (len(bad) == 0), bad


def _fmt_vec(v) -> str:
    return "(" + ", ".join(f"{x}" for x in v) + ")"


def main() -> int:
    ds = Dataset()
    glpsol = find_glpsol()
    banner("案例 F · 自研单纯形 vs 独立基准 vs GLPK", ds,
           对照求解器=(f"GLPK 5.0 ({glpsol})" if glpsol else "**未安装 —— 第三方对照将标记为 SKIP**"),
           独立基准="顶点枚举 / 射线证书 / RREF 一致性 + 手算预期（ORACLE）",
           说明="判定以 ORACLE 为准；三种独立基准按模型类型分派，逐条标注")

    section("F.1 模型与预期（预期值来源见源码注释）")
    rows = []
    for nm, o in ORACLE.items():
        rows.append([nm, o["status"],
                     f"{o['z']}" if o["z"] is not None else "—",
                     "唯一" if o["unique"] else ("多解" if o["unique"] is False else "—")])
    table(["模型", "预期状态", "预期最优值", "最优解"], rows, ["<", "<", ">", "<"], [28, 11, 12, 6])

    problems = build_problems(ds)
    chk = Checker("case_f")

    section("F.2 逐模型对照（独立基准按模型类型分派）")
    rows = []
    detail = []
    for p in problems:
        o = ORACLE[p.name]
        r_self = solve_self(p)
        r_enum = linalg.enumerate_optimum(p.c, p.A, p.b)
        r_glpk = solve_glpk(p, glpsol) if glpsol else None

        z_self = objective(p, r_self["x"])
        feas_self = verify_feasible(p, r_self["x"])

        # --- 自实现对 ORACLE ---
        st_ok = status_matches(r_self["status"], o["status"])
        z_ok = True
        if o["z"] is not None and r_self["status"] == "optimal":
            z_ok = z_self is not None and abs(Fraction(z_self).limit_denominator(10**6) - o["z"]) < Fraction(1, 10**6)
        feas_ok = True if r_self["x"] is None else feas_self[0]

        # --- 独立基准对 ORACLE（按模型类型选择，不能笼统说「枚举证明了全部」）---
        ray_cert = None
        if o["status"] == "optimal":
            method = "顶点枚举"
            indep_ok = (r_enum["status"] == "has_vertices" and r_enum["z_best"] == o["z"])
            indep_note = (f"枚举 z={r_enum['z_best']}（可行极点 {r_enum['n_feasible']} 个，"
                          f"有效行秩 {r_enum['rank']}/{len(p.A)}）")
        elif o["status"] == "infeasible":
            method = "RREF 一致性 / BFS 存在性"
            indep_ok = (r_enum["status"] == "infeasible")
            indep_note = f"判定 {r_enum['status']}（{r_enum.get('reason')}）"
        else:
            method = "射线证书（逐项代数验证）"
            w = RAY_CERTS.get(p.name)
            ray_cert = (linalg.verify_ray_certificate(p.c, p.A, p.b, w["x0"], w["d"])
                        if w else None)
            indep_ok = bool(ray_cert and ray_cert["ok"])
            indep_note = (f"x0={_fmt_vec(w['x0'])}、d={_fmt_vec(w['d'])}、c·d={ray_cert['cd']}"
                          if ray_cert else "无证书")
            # 顶点枚举只能给出极点上最小的 z，**不足以判定无界** —— 一并记录，防止误用
            indep_note += (f"；枚举器仅得极点 z={r_enum['z_best']}，不能作为无界证据")

        # --- GLPK 对 ORACLE（可选）---
        glpk_note = "SKIP（未安装）"
        if r_glpk is not None:
            g_ok = status_matches(r_glpk["status"], o["status"])
            if o["z"] is not None and r_glpk["status"] == "optimal":
                g_ok = g_ok and abs(objective(p, r_glpk["x"]) - float(o["z"])) <= 1e-5
            glpk_note = "一致" if g_ok else f"不一致({r_glpk['status']})"
        else:
            g_ok = None

        ok = bool(st_ok and z_ok and feas_ok and indep_ok) and (g_ok is not False)

        rows.append([
            p.name,
            r_self["status"],
            f"{z_self:.6f}" if z_self is not None else "—",
            method,
            "PASS" if ok else "FAIL",
        ])
        detail.append({"p": p, "o": o, "self": r_self, "enum": r_enum, "glpk": r_glpk,
                       "z_self": z_self, "indep_note": indep_note, "glpk_note": glpk_note,
                       "st_ok": st_ok, "z_ok": z_ok, "feas_ok": feas_ok,
                       "indep_ok": indep_ok, "g_ok": g_ok, "ok": ok,
                       "method": method, "ray": ray_cert})

    table(["模型", "自实现状态", "自实现 z", "独立基准", "判定"], rows,
          ["<", "<", ">", "<", "<"], [28, 14, 12, 26, 6])

    # 逐条打印对照细节
    for d in detail:
        p, o = d["p"], d["o"]
        print(f"  · {p.name}")
        print(f"      预期 {o['status']:<11} z={o['z'] if o['z'] is not None else '—'}")
        print(f"      自实现 {d['self']['status']:<11} z={d['z_self'] if d['z_self'] is not None else '—'}"
              f"   [{'ok' if d['st_ok'] else 'FAIL'}/{'ok' if d['z_ok'] else 'FAIL'}"
              f"/{'ok' if d['feas_ok'] else 'FAIL'}]")
        print(f"      独立基准（{d['method']}）{d['indep_note']}  [{'ok' if d['indep_ok'] else 'FAIL'}]")
        print(f"      GLPK {d['glpk_note']}")

    # 明确说明每个模型到底由什么验证，避免「枚举器证明了全部八个」这种笼统说法
    by_method = {}
    for d in detail:
        by_method.setdefault(d["method"], []).append(d["p"].name)
    print()
    print("  独立验证方法分布（8 个模型，逐项可查）:")
    for m, names in by_method.items():
        print(f"    {m:<24} {len(names)} 个: {', '.join(names)}")
    print("    * 注：无界模型**不能**由顶点枚举判定 —— 无界问题同样有可行极点，")
    print("      枚举只给出「极点上最小的 z」。F3 的无界性由射线证书证明。")

    section("F.3 迭代上限（maxiter）不得被冒充为 optimal / infeasible")
    # 复现审核给出的模型：min x, s.t. x+y=1。正确最优值 0。
    probe = LPProblem(name="F7 maxiter 探针", c=[1, 0], A=[[1, 1]], b=[1],
                      col_names=["x", "y"], row_names=["sum"])
    r1 = solve_self(probe, max_iter=1)
    r0 = solve_self(probe, max_iter=1)      # 同 r1，用于确认可重复
    chk.check("F7 预算不足时报 maxiter 而非 infeasible",
              r1["status"] == "maxiter" and r1["x"] is None and r1["z"] is None,
              f"max_iter=1 → {r1['status']}（phase={r1.get('phase')}），解为空")

    # 真正的性质不是「某一档预算必须 maxiter」，而是：
    #   对任意预算，状态要么是 maxiter（未算完），要么是 optimal 且值**确实最优**。
    # 旧实现会在 max_iter=2 时返回 optimal 且 z=1（真最优为 0），正是这里要拦住的。
    sweep = []
    bad = []
    for k in range(1, 7):
        r = solve_self(probe, max_iter=k)
        sweep.append((k, r["status"], r["z"]))
        if r["status"] == "maxiter":
            continue
        if r["status"] != "optimal":
            bad.append(f"k={k} 意外状态 {r['status']}")
        elif r["z"] is None or abs(r["z"]) > 1e-12:
            bad.append(f"k={k} 报 optimal 但 z={r['z']}（真最优 0）")
    print("    预算扫描（预期最优值 0）:")
    for k, st, z in sweep:
        print(f"      max_iter={k} → {st:<9} z={z if z is not None else '—'}")
    chk.check("F8 任何预算下「报 optimal 就必须是最优值」", not bad,
              "1..6 全部满足：maxiter 时无解、optimal 时值恒为 0" if not bad else "; ".join(bad))

    chk.check("F9 maxiter 不返回解（防止调用方误用）",
              r1["x"] is None and r0["x"] is None,
              "两次调用均无解返回，状态可重复")
    r_full = solve_self(probe, max_iter=5000)
    chk.check("F10 预算充足时恢复正常最优",
              r_full["status"] == "optimal" and abs(r_full["z"] or 0) < 1e-12,
              f"max_iter=5000 → {r_full['status']}, z={r_full['z']}（预期 0）")

    # ------------------------------------------------------------------ #
    section("F.4 无界证书与顶点枚举的反例回归（v4 新增）")

    # F12：审核给出的有界反例 —— 旧列判据误报
    c12, A12, b12 = [-1], [[-1]], [-1]
    legacy12 = linalg.legacy_column_ray_unsound(c12, A12)
    pr12 = linalg.prove_unbounded(c12, A12, b12)
    s12 = linalg.simplex_min(c12, A12, b12)
    print("    模型: min -x  s.t.  -x = -1,  x >= 0   （唯一解 x=1，z=-1，显然有界）")
    print(f"      旧列判据（c_j<0 且 A[:,j]<=0）: {legacy12}   ← 误报")
    print(f"      新证书判定: {pr12['status']} —— {pr12.get('reason')}")
    print(f"      单纯形: {s12['status']}, z={s12['z']}, x={[str(v) for v in (s12['x'] or [])]}")
    chk.check("F12 有界反例：旧判据误报，新证书判为有界",
              legacy12 and pr12["status"] == "bounded"
              and s12["status"] == "optimal" and s12["z"] == -1,
              "旧判据 True（错）；新判定 bounded；单纯形 optimal z=-1（正确）")

    # F13：F3 的证书逐项验证（x0=(0,0)、d=(1,1)，不是从求解器抄的）
    cert13 = linalg.verify_ray_certificate([-1, -1], [[1, -1]], [0], [0, 0], [1, 1])
    print("    F3 证书逐项验证（x0=(0,0)、d=(1,1)）:")
    for c in cert13["checks"]:
        print(f"      [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    chk.check("F13 F3 无界证书六条条件全部成立",
              cert13["ok"] and cert13["cd"] == -2,
              f"A·x0=b、x0>=0、A·d=0、d>=0、d≠0、c·d={cert13['cd']}<0 逐项通过，"
              f"采样点可行且目标严格下降")

    # F14：真无界，但不存在「单位列」射线 —— 旧判据会漏报
    c14, A14, b14 = [-1, 0, 0], [[1, -1, -1]], [0]
    legacy14 = linalg.legacy_column_ray_unsound(c14, A14)
    pr14 = linalg.prove_unbounded(c14, A14, b14)
    cert14 = pr14.get("certificate")
    print("    模型: min -x  s.t.  x - y - z = 0,  x,y,z >= 0")
    print("      射线 d=(1,1,0) 不是任何单位列；旧列判据必然漏报")
    print(f"      旧列判据: {legacy14}   ← 漏报")
    print(f"      新证书判定: {pr14['status']}, d={[str(v) for v in (cert14['d'] if cert14 else [])]}, "
          f"c·d={pr14.get('cd')}")
    chk.check("F14 非单位列的真无界（旧判据漏报，新证书成立）",
              (not legacy14) and pr14["status"] == "unbounded"
              and bool(cert14 and cert14["ok"]),
              "旧判据 False（漏）；新判定 unbounded 且证书逐项通过")

    # F15：方向存在，但问题根本不可行 —— 不能报无界
    c15, A15, b15 = [-1], [[-1]], [1]
    legacy15 = linalg.legacy_column_ray_unsound(c15, A15)
    pr15 = linalg.prove_unbounded(c15, A15, b15)
    s15 = linalg.simplex_min(c15, A15, b15)
    print("    模型: min -x  s.t.  -x = 1,  x >= 0   （x=-1，可行域为空）")
    print(f"      旧列判据: {legacy15}（列满足条件）但问题不可行")
    print(f"      新证书判定: {pr15['status']} —— {pr15.get('reason')}")
    chk.check("F15 方向存在但无可行点 ⇒ 不可行，不是无界",
              legacy15 and pr15["status"] == "infeasible" and s15["status"] == "infeasible",
              "先验可行性再验方向：结论为 infeasible，未误报 unbounded")

    # F16-F18：冗余约束下的顶点枚举
    e16 = linalg.enumerate_optimum([1], [[1], [1]], [1, 1])
    print("    模型: min x  s.t.  x=1, x=1, x>=0   （重复约束，可行）")
    print(f"      枚举 → {e16['status']}, z={e16['z_best']}, 可行极点 {e16['n_feasible']}, "
          f"有效行秩 {e16['rank']}/{2}, 使用行 {e16['rows_used']}")
    chk.check("F16 重复约束但可行：枚举仍求得 z=1",
              e16["status"] == "has_vertices" and e16["z_best"] == 1 and e16["rank"] == 1,
              f"rank=1（丢弃重复行），枚举到 {e16['n_feasible']} 个可行极点，z={e16['z_best']}")

    e17 = linalg.enumerate_optimum([1], [[1], [1]], [1, 2])
    print("    模型: min x  s.t.  x=1, x=2, x>=0   （重复左端、右端矛盾）")
    print(f"      枚举 → {e17['status']}（{e17.get('reason')}）")
    chk.check("F17 重复左端但右端矛盾：判 infeasible（RREF 矛盾）",
              e17["status"] == "infeasible" and e17.get("reason") == "rref_contradiction",
              f"{e17['status']}/{e17.get('reason')}")

    e18 = linalg.enumerate_optimum([1], [[1], [2]], [1, 2])
    print("    模型: min x  s.t.  x=1, 2x=2, x>=0   （线性组合冗余）")
    print(f"      枚举 → {e18['status']}, z={e18['z_best']}, 有效行秩 {e18['rank']}/{2}")
    chk.check("F18 线性组合冗余（2x=2）：枚举仍求得 z=1",
              e18["status"] == "has_vertices" and e18["z_best"] == 1,
              f"rank={e18['rank']}，z={e18['z_best']}")

    # F19：规模超限时必须说「未判定」，不能冒充不可行
    e19 = linalg.enumerate_optimum([1, 1], [[1, 1]], [1], max_bases=0)
    chk.check("F19 枚举预算不足时返回 too_large（结论未知，不得当作不可行）",
              e19["status"] == "too_large",
              f"max_bases=0 → {e19['status']}，未给出 z、也未判 infeasible")

    section("F.5 判定")
    for d in detail:
        p = d["p"]
        chk.check(f"{p.name.split()[0]} {p.name.split(' ', 1)[1]}", d["ok"],
                  f"状态 {d['self']['status']}（预期 {d['o']['status']}）；"
                  f"{d['method']}：{d['indep_note']}；GLPK {d['glpk_note']}")

    # 第三方对照：未执行则明确 SKIP，而不是算作通过
    if glpsol:
        all_g = all(d["g_ok"] for d in detail)
        chk.check("F20 第三方对照（GLPK）", all_g,
                  "8 个模型在状态与目标值上与 GLPK 全部一致" if all_g else "存在与 GLPK 不一致的模型")
    else:
        chk.skip("F20 第三方对照（GLPK）", "未安装 glpsol，本次未执行第三方对照")

    chk.summary()

    # ---- 结论文本由本次执行结果生成，不写死 ---- #
    n_pass = sum(1 for d in detail if d["ok"])
    print()
    print("  本次实际结果:")
    print(f"    * 自实现与 ORACLE 一致: {n_pass}/{len(detail)} 个模型")
    print(f"    * 独立验证方法: " + "；".join(f"{m} {len(v)} 个" for m, v in by_method.items()))
    print(f"    * 第三方对照: {'已完成（GLPK）' if glpsol else '未执行（未安装 GLPK）→ 已标记 SKIP'}")
    print(f"    * 迭代上限状态: 见 F7-F10")
    print(f"    * 证书/枚举反例回归: 见 F12-F19")
    print()
    print("  ★ 无论本次结果如何，以下事实不变（不得因通过而淡化）:")
    print("    * TypeScript 正式实现尚未编写，本案例只证明 Python 原型的算法思路；")
    print("    * 规模边界未测（本案例最大 3 约束 × 9 变量）；")
    print("    * 因此「自研 LP」仍为**候选方案**，不是锁定选型。")
    return chk.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
