#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dyson Canvas P0 —— 戴森球计划生产数据模型与递归求解器（零依赖实现）

设计约束:
  * 仅使用 Python 标准库（无 numpy / 无第三方包）。
  * 数值用 float 计算，但整数比例与守恒校验用 Fraction 精确复核。
  * 所有公式均在报告中标注来源（源码路径 + 函数名），本文件是被验证的**独立实现**，
    不是对参考项目源码的复制。

公式对齐（源码依据见报告第 3 章）:
  machines = (rate / (out_amount * out_mult)) * (time / (machine.speed * speed_mult))
  与 DSP Helper `solveNode()` 第 94-102 行等价。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, Iterable, List, Optional, Set, Tuple

EPS = 1e-9

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SUBSET = os.path.join(os.path.dirname(HERE), "dsp-subset.json")


# --------------------------------------------------------------------------- #
# 数据集
# --------------------------------------------------------------------------- #
class Dataset:
    """加载 dsp-subset.json，并提供索引。"""

    def __init__(self, path: str = DEFAULT_SUBSET):
        raw = json.load(open(path, encoding="utf-8"))
        self.path = path
        self.provenance = raw["_provenance"]
        gv = raw["_provenance"]["game_version"]
        self.version = gv.get("DSP", str(gv)) if isinstance(gv, dict) else str(gv)
        self.items: Dict[str, dict] = raw["items"]
        self.recipes: Dict[str, dict] = raw["recipes"]
        self.machines: Dict[str, dict] = raw["machines"]
        self.modules: Dict[str, dict] = raw["modules"]
        self.limitations: Dict[str, list] = raw.get("limitations", {})
        self._build_index()

    def _build_index(self) -> None:
        # 产出索引：item -> [recipe_id]，按 FactorsioLab 的 excludedRecipes 语义，
        # 这里不做排除，仅记录原始顺序；调用方必须显式指定用哪条配方。
        self.produced_by: Dict[str, List[str]] = {}
        self.consumed_by: Dict[str, List[str]] = {}
        for rid, r in self.recipes.items():
            for it in r["out"]:
                self.produced_by.setdefault(it, []).append(rid)
            for it in r["in"]:
                self.consumed_by.setdefault(it, []).append(rid)
        for v in self.produced_by.values():
            v.sort()
        for v in self.consumed_by.values():
            v.sort()

    def name(self, item_id: str) -> str:
        return self.items.get(item_id, {}).get("name", item_id)

    def machine_name(self, mid: str) -> str:
        return self.machines.get(mid, {}).get("name", mid)

    def is_raw(self, item_id: str) -> bool:
        """无任何配方可产出 => 真正的叶子资源（矿脉 / 液体 / 冰巨星气体）。"""
        return not self.produced_by.get(item_id)

    def producer_machines(self, recipe_id: str) -> List[str]:
        r = self.recipes[recipe_id]
        return [m for m in r["producers"] if m in self.machines]

    def pick_machine(self, recipe_id: str, preferred: Optional[Iterable[str]] = None) -> Optional[str]:
        """按调用方给定的优先序选机器，未命中则取 producers 中第一个可用机器。"""
        avail = self.producer_machines(recipe_id)
        if not avail:
            return None
        for m in (preferred or []):
            if m in avail:
                return m
        return avail[0]

    def primary_out(self, recipe_id: str) -> str:
        """主产物 = 单次产出量最大的物品（与 0fuz/dsp-calc `primaryOut()` 同构）。"""
        out = self.recipes[recipe_id]["out"]
        return max(out.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def recipes_for(self, item_id: str) -> List[str]:
        return list(self.produced_by.get(item_id, []))


# --------------------------------------------------------------------------- #
# 有效配方（建筑 + 增产剂）
# --------------------------------------------------------------------------- #
@dataclass
class Effective:
    recipe_id: str
    machine_id: Optional[str]
    speed_mult: float          # 建筑速度 × 增产剂加速
    out_mult: float            # 增产剂额外产出
    power_mult: float          # 增产剂耗电倍率 (1 + consumption)
    time_eff: float            # 单次有效工时 (秒)
    usage_kw: float
    drain_kw: float


def make_effective(ds: Dataset, recipe_id: str, machine_id: Optional[str],
                   module_id: Optional[str]) -> Effective:
    rec = ds.recipes[recipe_id]
    mach = ds.machines.get(machine_id) if machine_id else None
    base_speed = mach["speed"] if mach else 1.0

    speed_mult = 1.0
    out_mult = 1.0
    power_mult = 1.0
    if module_id and module_id in ds.modules:
        mo = ds.modules[module_id]
        speed_mult = 1.0 + mo.get("speed", 0.0)
        out_mult = 1.0 + mo.get("productivity", 0.0)
        power_mult = 1.0 + mo.get("consumption", 0.0)

    return Effective(
        recipe_id=recipe_id,
        machine_id=machine_id,
        speed_mult=base_speed * speed_mult,
        out_mult=out_mult,
        power_mult=power_mult,
        time_eff=rec["time"] / (base_speed * speed_mult),
        usage_kw=(mach["usage_kw"] if mach else 0.0) * power_mult,
        drain_kw=(mach["drain_kw"] if mach else 0.0) * power_mult,
    )


# --------------------------------------------------------------------------- #
# 递归求解器
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    item: str
    rate: float                       # 目标速率 items/s
    kind: str                         # craft | raw | cycle | cutoff | mine
    recipe: Optional[str] = None
    machine: Optional[str] = None
    cycles_per_s: float = 0.0
    machines: float = 0.0             # 小数建筑数（理论值）
    machines_used: float = 0.0        # 按取整策略实际纳入上游的倍数依据
    power_kw: float = 0.0
    children: List["Node"] = field(default_factory=list)
    note: str = ""


@dataclass
class Plan:
    root: Node
    machines: Dict[str, float]        # machine_id -> 台数（小数，已按机器合并）
    raw: Dict[str, float]             # item_id -> 外部购入/开采速率 items/s
    power_kw: float
    net: Dict[str, float]             # item_id -> 净产出(产-耗)，仅统计已展开节点
    purchased: Set[str] = field(default_factory=set)
    mine_machines: Dict[str, float] = field(default_factory=dict)  # 矿脉开采设备，与制造设备分列
    consumed: Dict[str, float] = field(default_factory=dict)       # item_id -> 被下游消费的总速率
    warnings: List[str] = field(default_factory=list)
    # (recipe_id, machine_id, config_key) -> 小数台数
    # 整数化必须按这个粒度取整，不能先按机器合并：两条不同配方各需 1/3 台同型机器，
    # 合并后 2/3 取整得 1，而实际要各建 1 台共 2 台。
    machine_groups: Dict[Tuple[str, str, str], float] = field(default_factory=dict)

    def machines_ceil(self) -> Dict[str, int]:
        """
        按「配方 × 机器 × 运行配置」分组取整后再按机器汇总。

        这是「固定配方持续生产」场景的正确口径：
          - 不同配方不能用同一台机器同时生产，必须各自建线；
          - 同一配方在多处被消费时（共享中间产物），走的是同一条产线，可以合并。
        """
        import math
        import faults
        out: Dict[str, int] = {}
        if faults.enabled("machines_merge"):
            self.machine_groups = {}      # 故障注入：退回「先按机器合并再取整」
        if self.machine_groups:
            for (_rid, mid, _cfg), v in self.machine_groups.items():
                out[mid] = out.get(mid, 0) + int(math.ceil(v - 1e-9))
        else:
            # 兼容旧路径：没有分组信息时退化为按机器合并取整（会在报告中标明）
            for k, v in self.machines.items():
                out[k] = int(math.ceil(v - 1e-9))
        return out

    def machine_groups_by_recipe(self) -> List[Tuple[str, str, float, int]]:
        """返回 [(recipe, machine, 小数台数, 取整台数)]，供报告逐条展示取整损失。"""
        import math
        rows = []
        for (rid, mid, _cfg), v in sorted(self.machine_groups.items(),
                                          key=lambda kv: (-kv[1], kv[0])):
            rows.append((rid, mid, v, int(math.ceil(v - 1e-9))))
        return rows


class RecursiveSolver:
    """
    沿「单一主配方」展开的递归求解器。

    与 DSP Helper `solveNode()` 的关键差异（有意为之，用于横向对照）:
      * visited 集合按**路径**复制，而非全局共享 —— 共享中间产物不会被误判为环。
      * 提供 rounding="exact"|"rounded" 两种取整传播策略。
      * 显式区分「矿脉配方」与「无配方物品」，支持 mining_policy="cut"|"expand"。

    为什么需要 mining_policy:
      FactorioLab 的 DSP 数据把矿脉建模为 flags=['mining'] 且输入为空的配方
      （iron-vein / copper-vein / ocean / gas-giant …）。若不加区分地递归，
      每一种矿石都会被展开成一台采矿机，玩家想看的「需要多少矿石」就消失了。
      真实工具（dsp-production-chain）的做法正是提供「终止层」选项。
    """

    def __init__(self, ds: Dataset, module_id: Optional[str] = None,
                 machine_pref: Optional[Iterable[str]] = None,
                 recipe_override: Optional[Dict[str, str]] = None,
                 rounding: str = "exact",
                 mining_policy: str = "cut",
                 cutoff: Optional[Iterable[str]] = None,
                 max_depth: int = 32):
        assert rounding in ("exact", "rounded")
        assert mining_policy in ("cut", "expand")
        self.ds = ds
        self.module_id = module_id
        self.machine_pref = list(machine_pref or [])
        self.recipe_override = dict(recipe_override or {})
        self.rounding = rounding
        self.mining_policy = mining_policy
        self.cutoff = set(cutoff or ())
        self.max_depth = max_depth

    # -- 选配方 ---------------------------------------------------------- #
    def _recipe_for(self, item: str) -> Optional[str]:
        if item in self.recipe_override:
            rid = self.recipe_override[item]
            return rid if item in self.ds.recipes[rid]["out"] else None
        cands = self.ds.recipes_for(item)
        return cands[0] if cands else None

    def solve(self, target: str, rate: float) -> Plan:
        machines: Dict[str, float] = {}
        raw: Dict[str, float] = {}
        net: Dict[str, float] = {}
        power = [0.0]
        warnings: List[str] = []
        purchased: Set[str] = set()
        mine_machines: Dict[str, float] = {}
        consumed: Dict[str, float] = {}
        groups: Dict[Tuple[str, str, str], float] = {}

        root = self._node(target, rate, frozenset(), 0, machines, groups, raw, net,
                          power, warnings, purchased, mine_machines, consumed)
        # 目标自身即为外部供给时（例如直接指定矿石为目标），父节点不存在，需补记
        if root.kind in ("raw", "cutoff", "cycle", "mine"):
            net[target] = net.get(target, 0.0) - rate
        return Plan(root=root, machines=machines, raw=raw, power_kw=power[0],
                    net=net, purchased=purchased, mine_machines=mine_machines,
                    consumed=consumed, warnings=warnings, machine_groups=groups)

    def solve_multi(self, targets: Dict[str, float]) -> Plan:
        """
        多目标共享求解：所有目标共用同一套累加器。

        这是「避免重复计算」的最小实现 —— 同一中间产物被多个目标消费时，
        machines / raw / consumed 只累加一次，而不是各建一条独立产线。
        """
        machines: Dict[str, float] = {}
        raw: Dict[str, float] = {}
        net: Dict[str, float] = {}
        power = [0.0]
        warnings: List[str] = []
        purchased: Set[str] = set()
        mine_machines: Dict[str, float] = {}
        consumed: Dict[str, float] = {}
        groups: Dict[Tuple[str, str, str], float] = {}

        roots: List[Node] = []
        for item, rate in targets.items():
            roots.append(self._node(item, rate, frozenset(), 0, machines, groups, raw,
                                    net, power, warnings, purchased, mine_machines,
                                    consumed))
            if roots[-1].kind in ("raw", "cutoff", "cycle", "mine"):
                net[item] = net.get(item, 0.0) - rate

        root = roots[0] if len(roots) == 1 else Node(
            item="<multi>", rate=sum(targets.values()), kind="multi",
            note=f"多目标: {', '.join(targets)}")
        if len(roots) > 1:
            root.children = roots
        return Plan(root=root, machines=machines, raw=raw, power_kw=power[0],
                    net=net, purchased=purchased, mine_machines=mine_machines,
                    consumed=consumed, warnings=warnings, machine_groups=groups)

    def _node(self, item: str, rate: float, visited: frozenset, depth: int,
              machines: Dict[str, float], groups: Dict[Tuple[str, str, str], float],
              raw: Dict[str, float],
              net: Dict[str, float], power: List[float],
              warnings: List[str], purchased: Set[str],
              mine_machines: Dict[str, float],
              consumed: Dict[str, float]) -> Node:
        if rate <= EPS:
            return Node(item=item, rate=rate, kind="cutoff", note="零速率")

        def as_supply(kind: str, note: str = "") -> Node:
            # 注意: 消费量已由父节点记入 net，此处只登记「外部供给量」。
            # 若重复扣减会导致净流量翻倍 —— 这一点在案例 A 首轮运行中被守恒校验捕获。
            raw[item] = raw.get(item, 0.0) + rate
            purchased.add(item)
            return Node(item=item, rate=rate, kind=kind, note=note)

        if item in self.cutoff:
            return as_supply("cutoff", "用户指定终止层")

        rid = self._recipe_for(item)
        if rid is None:
            return as_supply("raw")

        if item in visited:
            # 环：此处不展开，记为零成本供给（该行为是待批判的对象，见报告）
            return as_supply("cycle", "环内物品被当作外部供给")

        if depth > self.max_depth:
            return as_supply("cutoff", "达到深度上限")

        rec = self.ds.recipes[rid]
        is_mining = "mining" in rec.get("flags", [])
        if is_mining and self.mining_policy == "cut":
            # 到矿脉为止：矿石记为外购，但仍给出采矿设备需求供参考
            mid = self.ds.pick_machine(rid, self.machine_pref)
            eff = make_effective(self.ds, rid, mid, None)
            out_amt = rec["out"][item]
            cycles = rate / out_amt
            mach = cycles * eff.time_eff
            if mid:
                mine_machines[mid] = mine_machines.get(mid, 0.0) + mach
            node = as_supply("mine", f"矿脉（{self.ds.machine_name(mid or '?')} {mach:.3f} 台，单列统计）")
            node.recipe = rid
            node.machine = mid
            node.machines = mach
            node.cycles_per_s = cycles
            node.power_kw = mach * eff.usage_kw
            return node

        mid = self.ds.pick_machine(rid, self.machine_pref)
        eff = make_effective(self.ds, rid, mid, self.module_id)
        out_amount = rec["out"][item] * eff.out_mult
        cycles = rate / out_amount
        mach = cycles * eff.time_eff

        if mid:
            machines[mid] = machines.get(mid, 0.0) + mach
            # 分组键含运行配置（增产剂等级），不同配置的机器不能混算
            cfg = self.module_id or "-"
            groups[(rid, mid, cfg)] = groups.get((rid, mid, cfg), 0.0) + mach
        p = mach * eff.usage_kw
        power[0] += p

        # 产出侧净流量：所有产物都按同一运行次数计入
        for o, amt in rec["out"].items():
            net[o] = net.get(o, 0.0) + amt * eff.out_mult * cycles
        for i, amt in rec["in"].items():
            net[i] = net.get(i, 0.0) - amt * cycles

        mult = 1.0
        if self.rounding == "rounded" and mach > 0:
            import math
            mult = math.ceil(mach - 1e-9) / mach

        node = Node(item=item, rate=rate, kind="mine" if is_mining else "craft",
                    recipe=rid, machine=mid,
                    cycles_per_s=cycles, machines=mach, machines_used=mach * mult,
                    power_kw=p)

        nv = visited | {item}
        for i, amt in rec["in"].items():
            child_rate = amt * cycles * mult
            consumed[i] = consumed.get(i, 0.0) + child_rate
            child = self._node(i, child_rate, nv, depth + 1,
                               machines, groups, raw, net, power, warnings, purchased,
                               mine_machines, consumed)
            node.children.append(child)
        return node


# --------------------------------------------------------------------------- #
# 校验工具
# --------------------------------------------------------------------------- #
def check_conservation(plan: Plan, target: str, rate: float,
                       tol: float = 1e-6) -> Tuple[bool, Dict[str, float]]:
    """
    物料守恒校验：
      * 目标物品净流量应为 +rate
      * 外购/开采的原料净流量应为 -purchase（系统外部注入）
      * 其余中间产物净流量应 ≈ 0
    返回 (是否通过, 偏差字典)。
    """
    dev: Dict[str, float] = {}
    for item, v in plan.net.items():
        if item == target:
            expect = rate
        elif item in plan.purchased:
            expect = -plan.raw.get(item, 0.0)
        else:
            expect = 0.0
        d = v - expect
        if abs(d) > tol:
            dev[item] = d
    return (len(dev) == 0), dev


def fmt(x: float, nd: int = 3) -> str:
    if x == 0:
        return "0"
    if abs(x) >= 1000:
        return f"{x:,.1f}"
    return f"{x:.{nd}f}".rstrip("0").rstrip(".") if "." in f"{x:.{nd}f}" else f"{x:.{nd}f}"


def ratio(values: List[float], max_den: int = 10000) -> List[int]:
    """把一组浮点比例化为最小整数比（Fraction 精确化，避免浮点误差）。"""
    fr = [Fraction(v).limit_denominator(max_den) for v in values]
    den = 1
    for f in fr:
        den = den * f.denominator // _gcd(den, f.denominator)
    ints = [int(f * den) for f in fr]
    g = 0
    for i in ints:
        g = _gcd(g, abs(i))
    if g > 1:
        ints = [i // g for i in ints]
    return ints


def _gcd(a: int, b: int) -> int:
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a or 1


def print_header(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def dump_tree(node: Node, ds: Dataset, indent: int = 0, out=None) -> None:
    pad = "  " * indent
    label = {"craft": "制造", "raw": "原料", "mine": "矿脉",
             "cycle": "环未展开", "cutoff": "截断", "multi": "多目标"}[node.kind]
    line = f"{pad}- [{label}] {ds.name(node.item):<22} {fmt(node.rate):>10}/s"
    if node.kind in ("craft", "mine") and node.recipe:
        line += f"   配方={node.recipe:<26} 建筑={ds.machine_name(node.machine or '?'):<22}"
        line += f" 台数={node.machines:.4f}"
    if node.note:
        line += f"   ({node.note})"
    print(line, file=out)
    for c in node.children:
        dump_tree(c, ds, indent + 1, out)
