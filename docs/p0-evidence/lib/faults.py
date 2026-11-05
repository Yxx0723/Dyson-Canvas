#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
故障注入（fault injection）

为什么需要它
------------
v2 的三个案例在结尾无条件 `return 0`，导致「日志写 FAIL、脚本报 PASS」。
只把这些改成 `return 1` 还不够 —— 必须证明：**把被测逻辑改坏之后，程序确实会失败**。
否则我们无法区分「断言真的生效」与「断言永远不会触发」。

用法
----
通过环境变量 `P0_FAULT` 注入，逗号分隔，可叠加：

    P0_FAULT=lp_all_infeasible python3 case_f_lp_validation.py   # 应返回非零

每个注入点都是**最小、可定位的**：只改坏一条具体规则，不做随机扰动。
`verify_faults.py` 会逐一执行并核对退出码。
"""
from __future__ import annotations

import os
from typing import Set


def active() -> Set[str]:
    raw = os.environ.get("P0_FAULT", "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def enabled(name: str) -> bool:
    return name in active()


def describe() -> str:
    a = active()
    return "无" if not a else ", ".join(sorted(a))


# 注入点清单（供 verify_faults.py 与文档引用）
FAULTS = {
    "lp_all_infeasible": "自实现 LP 对所有模型返回 infeasible —— 验证案例 F 的预期状态检查真的会失败",
    "lp_maxiter": "自实现 LP 强制 max_iter=1 —— 验证「未完成」不会被冒充为 optimal/infeasible",
    "graph_missing_output": "构图退化为旧实现（只为输入建键）—— 验证节点统计的出现遗漏",
    "machines_merge": "设备取整退回「先按机器合并再取整」—— 验证整数台数口径",
    "policy_fuel_all": "把「有 fuel 字段」一律当成可燃 —— 验证燃料类型匹配",
    "scc_none": "SCC 检测恒返回空 —— 验证案例 D 的循环存在性断言",
    # ---- v4 新增：守住本轮修掉的三处逻辑 ---- #
    "ray_column_only": "无界判定退回「只看单列系数」—— 验证有界反例 F12 / 不可行 F15 会被抓住",
    "enumerate_raw_rows": "顶点枚举退回「按原始行数选列」—— 验证重复约束 F16 会被抓住",
    "boundary_no_byproduct": "环外展开不抵扣副产 —— 验证 H7/H8 的净需求与全局账会被抓住",
}
