#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
处置策略：从数据推导「哪些物品可以被消化掉、由谁消化」

为什么需要这个模块
------------------
v2 报告曾把「物品带 fuel 字段」直接等同于「可以送进火力发电站烧掉」。
这是错的。真实约束写在建筑的 machine 区块里：

    thermal-power-plant  : fuelTypes = ["chemical"]
    mini-fusion-power-plant: fuelTypes = ["nuclear"]
    artificial-star      : fuelTypes = ["antimatter"]
    ray-receiver-pro     : consumption = {"graviton-lens": 0.1}

也就是说，27 个带 fuel 的物品里只有 21 个是 chemical，能进火电站；
核燃料棒要进微型聚变发电站，反物质燃料棒要进人造恒星，
透镜根本不是燃料，是射线接收站的**运行消耗品**，
满蓄电池则要靠能源交换站放电。

本模块把「物品 → 可用的处置设施」这条边从数据里算出来，供 LP 的松弛列使用。
它不是策略本身，而是策略的**证据来源**；真正的取舍仍在业务层（见 report §4.8）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:                                   # pragma: no cover
    from dsp import Dataset


# 处置方式分类
KIND_BURN = "burn"          # 作为燃料燃烧，产出电力（本仓库不计入物料平衡，见 note）
KIND_CONSUME = "consume"    # 作为建筑的运行消耗品被吃掉
KIND_TRANSFORM = "transform"  # 经某条配方转化为另一种物品（如满电池→蓄电池）


@dataclass
class Route:
    """一条「该物品可以被谁消化」的路径。"""
    item: str
    kind: str
    facility: str            # 建筑 id
    fuel_type: str = ""      # burn 时可接受的燃料类型
    recipe: str = ""         # transform 时对应的配方
    rate_limit: Optional[float] = None   # consume 时的名义消耗速率（items/s，单台）
    note: str = ""

    def label(self) -> str:
        if self.kind == KIND_BURN:
            return f"燃烧 → {self.facility}（{self.fuel_type}）"
        if self.kind == KIND_CONSUME:
            return f"消耗 → {self.facility}" + (f"（{self.rate_limit}/s·台）" if self.rate_limit else "")
        return f"转化 → {self.recipe}"


@dataclass
class DisposalPolicy:
    """由数据集推导出的处置能力。"""
    routes: Dict[str, List[Route]] = field(default_factory=dict)
    # 诊断用
    fuel_items: Set[str] = field(default_factory=set)
    burnable: Set[str] = field(default_factory=set)
    consumable: Set[str] = field(default_factory=set)
    transformable: Set[str] = field(default_factory=set)
    unburnable_fuel: Set[str] = field(default_factory=set)   # 有 fuel 但烧不掉
    burn_by_type: Dict[str, Set[str]] = field(default_factory=dict)
    importable: Set[str] = field(default_factory=set)        # 有采集来源，可从外部获得

    # ---- 供 LP 使用的判据 ------------------------------------------------ #
    def can_dispose(self, item: str) -> bool:
        return bool(self.routes.get(item))

    def can_import(self, item: str) -> bool:
        """能否从外部获得：是否存在 mining 采集配方。"""
        return item in self.importable

    def can_burn_chemical(self, item: str) -> bool:
        """能否进火力发电站（唯一被本报告的 LP 示例用作「放空」的通道）。"""
        return any(r.kind == KIND_BURN and r.fuel_type == "chemical"
                   for r in self.routes.get(item, []))

    def dtachable(self) -> Set[str]:
        return set(self.routes)


def build_policy(ds: "Dataset") -> DisposalPolicy:
    """
    扫描机器区块与配方，构造处置路径。

    只做「数据里能查到依据」的推导，不做猜测：
      * burn      需要 machine.fuelTypes ∩ item.fuel.types ≠ ∅
      * consume   需要 machine.consumption 中出现该物品
      * transform 需要某条配方的输入含该物品、且该配方的 producer 是能源交换站一类设施
    """
    pol = DisposalPolicy()

    # ---- 0) 可外购：被 mining 配方产出的物品 ----
    for rid, r in ds.recipes.items():
        if "mining" in r.get("flags", []):
            pol.importable |= set(r["out"])

    # ---- 1) 燃烧类：建筑声明 fuel_types ----
    burners: List[Tuple[str, List[str]]] = []
    for mid, m in ds.machines.items():
        fts = (m or {}).get("fuel_types")
        if fts:
            burners.append((mid, list(fts)))

    for iid in ds.items:
        f = ds.items[iid].get("fuel")
        if not f:
            continue
        pol.fuel_items.add(iid)
        types = list(f.get("types") or [])
        for mid, fts in burners:
            hit = [t for t in types if t in fts]
            for t in hit:
                pol.routes.setdefault(iid, []).append(
                    Route(item=iid, kind=KIND_BURN, facility=mid, fuel_type=t,
                          note=f"热值 {f.get('value')}"))
                pol.burn_by_type.setdefault(t, set()).add(iid)
                pol.burnable.add(iid)

    # ---- 2) 消耗类：建筑声明 consumption ----
    for mid, m in ds.machines.items():
        for iid, rate in ((m or {}).get("consumption") or {}).items():
            pol.routes.setdefault(iid, []).append(
                Route(item=iid, kind=KIND_CONSUME, facility=mid, rate_limit=rate))
            pol.consumable.add(iid)

    # ---- 3) 转化类：配方输入 + 特定设施 ----
    #    只收「状态往返」性质的配方（如 accumulator-discharge），
    #    普通生产配方的输入不算处置路径 —— 那只是消耗，产物还在系统里。
    for rid, r in ds.recipes.items():
        for iid in r["in"]:
            if iid not in pol.fuel_items and iid not in pol.consumable:
                continue
            for mid in r.get("producers", []) or []:
                if "exchanger" in mid:
                    pol.routes.setdefault(iid, []).append(
                        Route(item=iid, kind=KIND_TRANSFORM, facility=mid, recipe=rid))
                    pol.transformable.add(iid)

    import faults
    if faults.enabled("policy_fuel_all"):
        # 故障注入：把「有 fuel 字段」一律当作可烧，正是 v2 报告的错误等价
        for iid in pol.fuel_items:
            pol.burnable.add(iid)
            pol.burn_by_type.setdefault("chemical", set()).add(iid)

    pol.unburnable_fuel = pol.fuel_items - pol.burnable - pol.consumable - pol.transformable
    return pol


def summarize(pol: DisposalPolicy, ds: "Dataset") -> List[Tuple[str, str, str]]:
    """给报告用的三列摘要：(类别, 数量, 物品列表)。"""
    rows = []
    rows.append(("带 fuel 字段的物品", str(len(pol.fuel_items)),
                 ", ".join(sorted(pol.fuel_items))))
    for t in sorted(pol.burn_by_type):
        rows.append((f"可燃烧 · {t}", str(len(pol.burn_by_type[t])),
                     ", ".join(sorted(pol.burn_by_type[t]))))
    rows.append(("可被建筑消耗（非燃料）", str(len(pol.consumable)),
                 ", ".join(sorted(pol.consumable))))
    rows.append(("可经配方转化", str(len(pol.transformable)),
                 ", ".join(sorted(pol.transformable))))
    if pol.unburnable_fuel:
        rows.append(("有 fuel 但无任何处置路径", str(len(pol.unburnable_fuel)),
                     ", ".join(sorted(pol.unburnable_fuel))))
    return rows
