#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
退出码自检：证明「断言真的会失败」

为什么需要这一步
----------------
把 `return 0` 改成 `return 1` 只是改了代码；它证明不了断言有效。
真正的问题是：**把被测逻辑改坏之后，程序会不会失败？**
如果不会，说明断言是装饰品 —— 日志里永远绿，但什么都没在守。

本脚本对每个关键断言做一次「改坏 → 观察退出码」的实验：

    1. 正常跑一遍，要求 exit == 0
    2. 注入故障跑一遍，要求 exit != 0
    3. 两者都满足，才算这一条守卫有效

故障通过环境变量 P0_FAULT 注入（见 lib/faults.py），每种只改坏一条具体规则。
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# (被测脚本, 注入的故障, 预期在日志中出现的失败标记)
CASES = [
    ("case_a_basic_chain.py", "machines_merge", "A3b 整数建设台数",
     "设备取整退回「先按机器合并」，A3b 应报 8 台而非 9 台"),
    ("case_d_hydrogen_loop.py", "scc_none", "D1 循环存在性",
     "循环检测返回空，D1 应失败"),
    ("case_f_lp_validation.py", "lp_all_infeasible", "FAIL",
     "求解器全报不可行，与 ORACLE 不符应失败"),
    ("case_f_lp_validation.py", "lp_maxiter", "FAIL",
     "强制预算 1，F10 应失败（否则 maxiter 又被当成 optimal）"),
    ("case_g_boundary_policy.py", "policy_fuel_all", "G1 燃料按类型分派设施",
     "把「有 fuel」当可燃，chemical 数量应变成 27 而非 21"),
    ("case_g_boundary_policy.py", "lp_all_infeasible", "G5 字典序解满足全部需求",
     "求解器无解时，G3-G6 应明确判失败而不是让脚本抛异常"),
    ("stats.py", "graph_missing_output", "未建键",
     "构图退化为旧实现，节点统计断言应触发"),
    # ---- v4 新增：守住本轮修掉的三处逻辑 ---- #
    ("case_f_lp_validation.py", "ray_column_only", "F12 有界反例",
     "无界判定退回「单列系数」，有界反例应被误报 → F12 必须失败"),
    ("case_f_lp_validation.py", "enumerate_raw_rows", "F16 重复约束但可行",
     "顶点枚举退回「按原始行数选列」，重复约束下应枚举失败 → F16 必须失败"),
    ("case_h_auto_boundary.py", "boundary_no_byproduct", "H7 氢净需求",
     "环外展开不抵扣副产，H2 净需求变回 4 → H7/H8 必须失败"),
]


def run(script: str, fault: str = "") -> tuple:
    env = dict(os.environ)
    env.pop("P0_FAULT", None)
    if fault:
        env["P0_FAULT"] = fault
    p = subprocess.run([sys.executable, script], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=180)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    print("=" * 78)
    print("退出码自检：正常路径必须成功，注入故障必须失败")
    print("=" * 78)
    rows = []
    ok_all = True

    for script, fault, marker, why in CASES:
        rc_ok, out_ok = run(script)
        rc_bad, out_bad = run(script, fault)
        normal_pass = rc_ok == 0
        fault_detected = rc_bad != 0
        marked = marker in out_bad
        good = normal_pass and fault_detected and marked
        ok_all &= good
        rows.append((script, fault, rc_ok, rc_bad, marked, good))
        print(f"\n  {script}  +  P0_FAULT={fault}")
        print(f"    目的       : {why}")
        print(f"    正常       : exit={rc_ok}  {'OK' if normal_pass else '异常（应成功）'}")
        print(f"    注入故障   : exit={rc_bad}  {'OK' if fault_detected else '未失败（断言是装饰品）'}")
        print(f"    失败标记   : {'命中 ' + marker if marked else '未命中 ' + marker}")
        print(f"    结论       : {'有效守卫' if good else '无效'}")

    print()
    print("-" * 78)
    n_ok = sum(1 for r in rows if r[-1])
    print(f"  有效守卫 {n_ok}/{len(rows)}")
    print(f"  总体: {'PASS' if ok_all else 'FAIL'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
