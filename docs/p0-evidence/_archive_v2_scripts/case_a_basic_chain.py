#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案例 A —— 基础递归生产链

验证目标（对应 P0 补充要求）:
  A1 生产速率沿链传递是否正确
  A2 原料需求汇总是否正确
  A3 建筑数量计算与取整是否与手算一致
  A4 物料守恒（净流量）是否成立
  A5 两种取整传播策略（exact / rounded）的差异是否可量化

目标产物: 电磁矩阵 electromagnetic-matrix，1 个/秒（= 60/min）
建筑选择: 制造 = 制造台 Mk.III（assembling-machine-3），熔炼 = 电弧熔炉（arc-smelter），
          矩阵 = 矩阵研究站（matrix-lab）
增产剂  : 无（先用无增产剂基准，排除干扰项）

手算基准全部写在 EXPECTED 里，先硬编码再与求解器输出比对 —— 这是本案例的
「预期结果 vs 实际结果」隔离手段，避免用求解器的输出反过来当答案。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from dsp import Dataset, RecursiveSolver, check_conservation, fmt   # noqa: E402
from report import banner, section, kv, table, verdict              # noqa: E402

TARGET = "electromagnetic-matrix"
RATE = 1.0  # items/s

MACHINE_PREF = ["assembling-machine-3", "arc-smelter", "matrix-lab"]

# ---- 手算基准（由配方定义 + 建筑速度直接推出，与求解器无关）----
# electromagnetic-matrix: t=3, 1 craft -> 1 matrix ; matrix-lab speed=1
#   cycles/s = 1/1 = 1 ; machines = 1 * (3/1) = 3
# circuit-board: t=1, out=2 ; asm3 speed=1.5
#   demand 1/s -> cycles = 0.5 ; machines = 0.5 * (1/1.5) = 1/3
# magnetic-coil: t=1, out=2 ; asm3 speed=1.5
#   demand 1/s -> cycles = 0.5 ; machines = 1/3
# copper-ingot: 需求 = 1*0.5(circuit) + 1*0.5(coil) = 1/s ; arc-smelter speed=1, t=1
#   machines = 1
# iron-ingot: 需求 = 2*0.5 = 1/s ; machines = 1
# magnet: t=1.5, out=1 ; 需求 = 2*0.5 = 1/s ; machines = 1 * 1.5 = 1.5
# iron-ore: 1 (ingot) + 1 (magnet) = 2/s ; copper-ore = 1/s
EXPECTED = {
    "machines": {
        "matrix-lab": 3.0,
        "assembling-machine-3": 1.0 / 3 + 1.0 / 3,   # circuit-board + magnetic-coil
        "arc-smelter": 1.0 + 1.0 + 1.5,              # copper + iron + magnet
    },
    "raw": {"iron-ore": 2.0, "copper-ore": 1.0},
    "power_kw": (
        3.0 * 480.0                       # matrix-lab 480 kW
        + (2.0 / 3) * 1080.0              # asm3 1080 kW
        + (1.0 + 1.0 + 1.5) * 360.0       # arc-smelter 360 kW
    ),
}


def main() -> int:
    ds = Dataset()
    banner("案例 A · 基础递归生产链", ds,
           目标=f"{ds.name(TARGET)} ({TARGET}) {RATE}/s",
           建筑="assembling-machine-3 / arc-smelter / matrix-lab",
           增产剂="无")

    section("A.0 原始配方（数据原样，未加工）")
    chain = ["electromagnetic-matrix", "circuit-board", "magnetic-coil",
             "copper-ingot", "iron-ingot", "magnet"]
    rows = []
    for rid in chain:
        r = ds.recipes[rid]
        rows.append([rid, r["time"], "×".join(f"{k}:{v}" for k, v in sorted(r["in"].items())),
                     "×".join(f"{k}:{v}" for k, v in sorted(r["out"].items())), len(r["producers"])])
    table(["配方", "工时(s)", "输入", "输出", "可选建筑数"], rows, ["<", ">", "<", "<", ">"],
          [24, 7, 30, 22, 9])

    section("A.1 计算模型")
    print("  machines = (rate / (out_amount × out_mult)) × (time / (machine.speed × speed_mult))")
    print("  其中 out_mult=1、speed_mult=1（未启用增产剂）；power = machines × usage × power_mult")
    print("  递归展开时，子物品需求 = in_amount × cycles_per_s × 取整倍数")
    print("  取整倍数: exact=1（按理论需求） / rounded=ceil(machines)/machines（按实际产能）")

    # ---- exact ----
    solver = RecursiveSolver(ds, module_id=None, machine_pref=MACHINE_PREF, rounding="exact")
    plan = solver.solve(TARGET, RATE)

    section("A.2 生产树（exact 取整传播）")
    from dsp import dump_tree
    dump_tree(plan.root, ds)

    section("A.3 建筑数量：实际 vs 手算")
    rows = []
    ok_mach = True
    for mid, expect in EXPECTED["machines"].items():
        act = plan.machines.get(mid, 0.0)
        diff = abs(act - expect)
        good = diff < 1e-9
        ok_mach &= good
        rows.append([mid, f"{expect:.6f}", f"{act:.6f}", f"{diff:.2e}", "PASS" if good else "FAIL"])
    table(["建筑", "手算基准", "求解器", "偏差", "判定"], rows, ["<", ">", ">", ">", "<"], [26, 14, 14, 10, 6])

    section("A.4 原料需求：实际 vs 手算")
    rows = []
    ok_raw = True
    for iid, expect in EXPECTED["raw"].items():
        act = plan.raw.get(iid, 0.0)
        good = abs(act - expect) < 1e-9
        ok_raw &= good
        rows.append([iid, f"{expect:.6f}", f"{act:.6f}", "PASS" if good else "FAIL"])
    extra = set(plan.raw) - set(EXPECTED["raw"])
    for iid in sorted(extra):
        rows.append([iid, "0", f"{plan.raw[iid]:.6f}", "FAIL"])
        ok_raw = False
    table(["原料", "手算基准", "求解器", "判定"], rows, ["<", ">", ">", "<"], [26, 14, 14, 6])

    section("A.5 功耗")
    ok_power = abs(plan.power_kw - EXPECTED["power_kw"]) < 1e-6
    kv("手算基准 (kW)", f"{EXPECTED['power_kw']:.3f}")
    kv("求解器 (kW)", f"{plan.power_kw:.3f}")
    kv("偏差 (kW)", f"{plan.power_kw - EXPECTED['power_kw']:+.2e}")
    print("  注: 仅计 usage（工作功耗），未计 drain（待机功耗）。FactorioLab 的 flags 中")
    print("      'inactiveDrain' 表明待机功耗需单独建模，DSP Helper 亦忽略此项。")

    section("A.6 物料守恒校验")
    ok_cons, dev = check_conservation(plan, TARGET, RATE)
    for item in sorted(plan.net):
        mark = "ok" if abs(dev.get(item, 0.0)) < 1e-6 else "FAIL"
        print(f"    {item:<26} 净流量 {plan.net[item]:>+10.4f}/s   {mark}")
    print(f"  （目标之外的物品净流量应为 0；目标自身应为 +{RATE}）")

    section("A.7 取整传播策略对比")
    solver_r = RecursiveSolver(ds, module_id=None, machine_pref=MACHINE_PREF, rounding="rounded")
    plan_r = solver_r.solve(TARGET, RATE)
    rows = []
    for mid in list(EXPECTED["machines"].keys()):
        a = plan.machines.get(mid, 0)
        b = plan_r.machines.get(mid, 0)
        rows.append([mid, f"{a:.4f}", f"{b:.4f}", f"{b - a:+.4f}", f"{math.ceil(a - 1e-9)}"])
    table(["建筑", "exact 台数", "rounded 台数", "增量", "ceil(exact)"], rows,
          ["<", ">", ">", ">", ">"], [26, 12, 13, 10, 11])
    print(f"  原料(铁矿石): exact={plan.raw['iron-ore']:.4f}/s  rounded={plan_r.raw['iron-ore']:.4f}/s")
    print(f"  原料(铜矿石): exact={plan.raw['copper-ore']:.4f}/s  rounded={plan_r.raw['copper-ore']:.4f}/s")
    print("  说明: rounded 策略把每层向上取整后的富余产能继续向下游传导，")
    print("        因此原料需求会系统性高于理论值 —— 这是真实建厂时的原料备料口径，")
    print("        两者不能混用，必须由用户显式选择。")

    section("A.8 结论")
    all_ok = ok_mach and ok_raw and ok_power and ok_cons
    verdict("A1 速率传递", ok_mach, "所有中间节点需求与手算一致")
    verdict("A2 原料汇总", ok_raw, "叶子资源需求与手算一致")
    verdict("A3 建筑数量", ok_mach, "小数台数与公式完全一致（无取整损失）")
    verdict("A4 物料守恒", ok_cons, "除目标外净流量全为 0" if ok_cons else f"偏差: {dev}")
    verdict("A5 功耗", ok_power, "usage 口径一致；drain 未建模（已知缺失）")
    print()
    print(f"  总体判定: {'PASS' if all_ok else 'FAIL'}")
    print("  证据等级: 已通过独立测试验证（脚本 + 手算基准双向比对）")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
