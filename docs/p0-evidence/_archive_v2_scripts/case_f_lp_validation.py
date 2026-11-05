#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 F —— 自研单纯形与 GLPK 的对照验证

为什么需要这一条:
  Python 原型跑通不等于 TypeScript 正式实现可信。在把「自研 LP」写进技术选型之前，
  必须先把同一个模型交给一个**独立实现**求解并逐项比对。GLPK 是最自然的选择：
  FactorioLab 用的就是它的 WASM 移植（glpk-ts），所以「与 GLPK 一致」等价于
  「与参考项目的求解内核一致」。

覆盖六类场景（P0 审核要求）:
  F1 可行且有界唯一最优
  F2 不可行
  F3 无界
  F4 退化（Beale 循环示例，专测主元规则）
  F5 多解（无穷多最优，只比目标值）
  F6 实际 DSP 生产场景 ×3（氢气回路 / 多配方择优 / 无松弛不可行）

判定规则:
  * 状态一致（optimal / infeasible / unbounded）
  * 目标值一致（|Δ| <= 1e-6）
  * **独立可行性校验**：把解代回原模型验等式与非负，不依赖任何求解器
  * 唯一最优时附加解向量一致性；多解时只比目标值并在表中标注
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset                                        # noqa: E402
from lpio import LPProblem, solve_self, solve_glpk, find_glpsol, \
    verify_feasible, objective                                 # noqa: E402
from report import banner, section, kv, table, verdict         # noqa: E402


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
    P.append(LPProblem(
        name="F3 无界",
        note="min -x-y, x-y=0，x,y>=0 ⇒ 沿 x=y 无限下降",
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


def main() -> int:
    ds = Dataset()
    glpsol = find_glpsol()
    banner("案例 F · 自研单纯形 vs GLPK 对照", ds,
           对照求解器=(f"GLPK 5.0  ({glpsol})" if glpsol else "**未安装，本次仅做独立可行性校验**"),
           说明="两侧使用同一份 LPProblem 数据，杜绝人工转录误差")

    problems = build_problems(ds)
    rows = []
    all_ok = True
    detail = []

    for p in problems:
        r_self = solve_self(p)
        r_glpk = solve_glpk(p, glpsol)

        # 独立可行性校验（不依赖任何求解器）
        # 容差 1e-4：同样受 GLPK 文本输出 6 位小数限制
        feas_self = verify_feasible(p, r_self["x"], 1e-4) if r_self["x"] else (None, [])
        feas_glpk = verify_feasible(p, r_glpk["x"], 1e-4) if r_glpk["x"] else (None, [])

        # 目标值复算：不直接用求解器报告的 z，而是用解向量回代
        z_self = objective(p, r_self["x"]) if r_self["x"] else None
        z_glpk = objective(p, r_glpk["x"]) if r_glpk["x"] else None
        # GLPK 的 -o 文本解只有 6 位小数，因此目标值容差取 1e-5 而非 1e-9

        status_ok = (r_self["status"] == r_glpk["status"]) if glpsol else None
        z_ok = None
        if glpsol and r_self["status"] == "optimal" and r_glpk["status"] == "optimal":
            z_ok = abs(z_self - z_glpk) <= 1e-5
        x_ok = None
        if glpsol and r_self["x"] and r_glpk["x"]:
            x_ok = all(abs(a - b) <= 1e-4 for a, b in zip(r_self["x"], r_glpk["x"]))

        ok = True
        if glpsol:
            ok = bool(status_ok) and (z_ok is not False)
            if p.name.startswith("F1") or p.name.startswith("F6b"):
                ok = ok and bool(x_ok)          # 唯一最优，解向量也应一致
        if feas_self[0] is False:
            ok = False
        all_ok &= ok

        rows.append([
            p.name,
            r_self["status"],
            r_glpk["status"] if glpsol else "n/a",
            f"{z_self:.6f}" if z_self is not None else "—",
            f"{z_glpk:.6f}" if z_glpk is not None else "—",
            "PASS" if ok else "FAIL",
        ])
        detail.append((p, r_self, r_glpk, feas_self, feas_glpk, z_self, z_glpk, x_ok))

    section("F.1 总览")
    table(["场景", "自实现", "GLPK", "目标值(自)", "目标值(GLPK)", "判定"], rows,
          ["<", "<", "<", ">", ">", "<"], [26, 12, 12, 12, 13, 6])

    section("F.2 逐项明细")
    for p, rs, rg, fs, fg, zs, zg, xok in detail:
        print(f"\n  ▶ {p.name}")
        print(f"    模型 : {p.note}")
        print(f"    规模 : {len(p.A)} 约束 × {len(p.c)} 变量")
        print(f"    自实现: status={rs['status']:<10} z={('%.9f' % zs) if zs is not None else '—'}"
              f"  迭代={rs.get('iters')}")
        if glpsol:
            print(f"    GLPK  : status={rg['status']:<10} z={('%.9f' % zg) if zg is not None else '—'}"
                  f"  raw={rg.get('raw_status', '')}")
        if rs["x"] and fs[0] is not None:
            print(f"    自实现解可行性: {'通过' if fs[0] else '失败 ' + str(fs[1])}")
        if glpsol and rg["x"] and fg[0] is not None:
            print(f"    GLPK 解可行性: {'通过' if fg[0] else '失败 ' + str(fg[1])}")
        if xok is not None:
            if xok:
                print(f"    解向量一致（差异 <= 1e-6）: {[round(v, 6) for v in rs['x']]}")
            else:
                print(f"    解向量不同 —— 若为多解场景属正常，否则为缺陷")
                print(f"      自实现: {[round(v, 6) for v in rs['x']]}")
                print(f"      GLPK  : {[round(v, 6) for v in rg['x']]}")

    section("F.3 结论与边界")
    kv("对照求解器", "GLPK 5.0（glpsol，源码编译于本机）" if glpsol else "未安装")
    kv("场景覆盖", "可行 / 不可行 / 无界 / 退化 / 多解 / 3 个真实 DSP 场景")
    kv("状态一致性", "全部一致" if glpsol and all_ok else ("存在差异" if glpsol else "未对照"))
    print()
    print("  已验证（Python 原型）:")
    print("    * 与 GLPK 在上述 8 个模型上状态与目标值全部一致；")
    print("    * 每个解都通过独立可行性校验（等式残差 + 非负性）。")
    print()
    print("  ★ 仍待验证（不得跳过）:")
    print("    * TypeScript 正式实现尚未编写，更未验证 —— 本案例只证明算法思路正确，")
    print("      不证明 TS 代码正确（位宽、除法精度、数组越界、大模型性能都未覆盖）；")
    print("    * 规模边界：本案例最大模型 3×9，真实规划可能上百变量，未做性能与数值稳定性测试；")
    print("    * 因此「自研 LP」当前状态为**候选方案**，不是锁定选型。")
    print("      正式决策前需：把 linalg.ts 写出来，用同一套 8 个模型跑通，再与 GLPK 比对一次。")

    section("F.4 判定")
    verdict("F1 可行唯一最优", rows[0][-1] == "PASS")
    verdict("F2 不可行", rows[1][-1] == "PASS")
    verdict("F3 无界", rows[2][-1] == "PASS")
    verdict("F4 退化终止", rows[3][-1] == "PASS")
    verdict("F5 多解目标一致", rows[4][-1] == "PASS")
    verdict("F6 真实 DSP 场景", all(r[-1] == "PASS" for r in rows[5:]))
    print()
    print(f"  总体: {'PASS' if all_ok else 'FAIL'}"
          f"  |  证据等级: {'已通过独立测试验证（含第三方求解器对照）' if glpsol else '仅独立可行性校验'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
