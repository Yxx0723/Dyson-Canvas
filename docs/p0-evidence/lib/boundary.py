#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
循环边界分析：从真实上下游图自动推导「求解子图 + 外部净需求 + 全局物料账」

为什么需要它
------------
v2 的案例 G 打印了 SCC 边界，但真正喂给 LP 的矩阵是**手写的三行三列**，
需求也是 `DEMAND = {"refined-oil": 6.5, ...}` 这样的常量。
两者之间没有任何程序连接 —— 也就是说「自动识别边界」这件事从未被验证过。

本模块把那条链路补上：

    目标产量 ──► 递归展开（遇到边界物品即停）──► 环外配方的实际运行速率
                        │                              │
                        │                              ├─► 边界物品的**消耗**
                        │                              └─► 边界物品的**副产**
                        │
                        └──► 配方选择（规则 A/B）──► 净流量矩阵 A

    b[i] = 环外消耗[i] + 目标直接需求[i] − 环外副产[i]

v4 修正（第三轮复验）
--------------------
v3 只累计「环外配方对边界物品的输入需求」，不抵扣这些配方的**副产输出**。
用当前数据即可复现：

    目标: 能量矩阵 2/s + 反物质 1/s
    能量矩阵配方 : 2 EG + 2 H2 → 1 能量矩阵        ⇒ 耗 H2 4/s
    质能储存配方 : 2 临界光子 → 2 反物质 + 2 H2    ⇒ 产 H2 1/s（同时是反物质的唯一来源）
    正确净需求   : 4 − 1 = 3/s

v3 把需求记成 4/s，油路也净供 4/s，于是整张图多出 1/s 氢气无人认领，
而 LP 自己的三行平衡仍然「通过」—— 局部守恒不能代替全局守恒。
现在：
  * `ExternalRun` 记录每条环外配方的**速率与全部输入/输出**；
  * 净需求按「消耗 − 副产」生成；副产超过消耗时显式记入 `surplus`，不静默丢弃；
  * `global_balance()` 把环外展开与环内 LP 拼成一张账，逐物品核对残差。

设计约束
--------
1. **配方由数据选出，不由人指定**（规则 A / 规则 B，见 `analyze`）。
2. **需求由递归展开算出**，且必须带副产抵扣。
3. **不假设边界物品可无限外购/放空**，松弛列是否可用由 policy 模块按数据判定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:                                             # pragma: no cover
    from dsp import Dataset


def _rat(x) -> Fraction:
    """把数据里的数值转成尽量干净的有理数（消除 1e-15 级浮点残差）。"""
    if isinstance(x, Fraction):
        return x
    if isinstance(x, int):
        return Fraction(x)
    f = Fraction(x)
    if f.denominator > 10 ** 6:
        f = Fraction(x).limit_denominator(10 ** 6)
    return f


@dataclass
class ExternalRun:
    """一条**环外**配方的实际运行情况（速率 + 全部输入输出）。"""
    recipe: str
    rate: Fraction
    inputs: Dict[str, Fraction] = field(default_factory=dict)
    outputs: Dict[str, Fraction] = field(default_factory=dict)
    kind: str = "craft"          # "craft" 有物料输入；"free" 无物料输入（如戴森云光子）

    def label(self) -> str:
        return f"{self.recipe} ×{float(self.rate):.4f}/s"


@dataclass
class Boundary:
    scc_items: List[str] = field(default_factory=list)        # 环内物品
    incoming: List[str] = field(default_factory=list)         # 环外供给环内
    outgoing: List[str] = field(default_factory=list)         # 环内供给环外
    recipes: List[str] = field(default_factory=list)          # 入选的环内配方
    excluded_recipes: List[Tuple[str, str]] = field(default_factory=list)
    rows: List[str] = field(default_factory=list)             # LP 平衡行
    demand: Dict[str, float] = field(default_factory=dict)    # 净外部需求（可为负）
    trace: List[str] = field(default_factory=list)            # 推导过程，供打印

    # ---- v4 新增：环外展开账本 ---- #
    external_runs: List[ExternalRun] = field(default_factory=list)
    gross_in: Dict[str, float] = field(default_factory=dict)   # 环外对边界物品的消耗
    gross_out: Dict[str, float] = field(default_factory=dict)  # 环外对边界物品的副产
    target_direct: Dict[str, float] = field(default_factory=dict)  # 目标本身即边界物品
    raw_inputs: Dict[str, float] = field(default_factory=dict)  # 无配方 → 外部原料
    surplus: Dict[str, float] = field(default_factory=dict)     # 副产 > 消耗 的差额
    targets: Dict[str, float] = field(default_factory=dict)

    @property
    def stop_items(self) -> Set[str]:
        """递归展开时应当停下的物品：环内物品 + 环内对外供应的物品。"""
        return set(self.scc_items) | set(self.outgoing)


def _primary_recipe(ds: "Dataset", item: str, exclude: Set[str]) -> Optional[str]:
    for rid in ds.recipes_for(item):
        if rid in exclude:
            continue
        return rid
    return None


def expand_external(ds: "Dataset", targets: Dict[str, float], stop: Set[str],
                    exclude_recipes: Set[str], max_depth: int = 64) -> Dict:
    """
    从目标产量出发递归展开**环外**子树，遇到 stop 集合中的物品即停止。

    与 v3 的差别：不只累计「需求」，而是按配方聚合实际运行速率，
    并分别记录每个边界物品的**外部消耗**与**外部副产**。

    返回 dict:
      runs         : OrderedDict[recipe] -> (rate, inputs, outputs)
      gross_in     : 边界物品被环外消耗的总量
      gross_out    : 边界物品被环外副产的总量
      target_direct: 目标本身就是边界物品（不再展开）
      raw          : 没有任何配方 → 视为外部原料（矿石/水/植物等）
      trace        : 逐行推导轨迹
    """
    runs: Dict[str, Dict] = {}
    gross_in: Dict[str, Fraction] = {}
    gross_out: Dict[str, Fraction] = {}
    target_direct: Dict[str, Fraction] = {}
    raw: Dict[str, Fraction] = {}
    trace: List[str] = []

    def bump(d: Dict[str, Fraction], k: str, v: Fraction) -> None:
        d[k] = d.get(k, Fraction(0)) + v

    def walk(item: str, rate: Fraction, depth: int, path: Tuple[str, ...],
             is_target: bool) -> None:
        if rate <= 0:
            return
        if item in stop:
            if is_target:
                bump(target_direct, item, rate)
                if depth <= 8:
                    trace.append(f"{'  ' * depth}⊣ {item} +{float(rate):.4f}/s"
                                 f"（目标本身即边界物品，停止展开）")
            else:
                bump(gross_in, item, rate)
                if depth <= 8:
                    trace.append(f"{'  ' * depth}⊣ {item} +{float(rate):.4f}/s"
                                 f"（边界物品，计入外部消耗）")
            return

        rid = _primary_recipe(ds, item, exclude_recipes)
        if rid is None:
            bump(raw, item, rate)
            if depth <= 8:
                trace.append(f"{'  ' * depth}· {item} {float(rate):.4f}/s"
                             f"（无配方，按外部原料处理）")
            return
        rec = ds.recipes[rid]
        out_amt = _rat(rec["out"][item])
        cycles = rate / out_amt
        slot = runs.setdefault(rid, {"rate": Fraction(0), "in": {}, "out": {}})
        slot["rate"] += cycles
        for i, amt in rec["in"].items():
            bump(slot["in"], i, _rat(amt) * cycles)
        for o, amt in rec["out"].items():
            bump(slot["out"], o, _rat(amt) * cycles)
        if depth <= 8:
            trace.append(f"{'  ' * depth}· {item} {float(rate):.4f}/s ← {rid} ×{float(cycles):.4f}")
        if depth > max_depth or item in path:
            trace.append(f"{'  ' * depth}! 展开深度超限或成环，已停止: {item}")
            return
        for i, amt in rec["in"].items():
            walk(i, _rat(amt) * cycles, depth + 1, path + (item,), False)

    for t, r in targets.items():
        walk(t, _rat(r), 0, (), True)

    # 环外配方对边界物品的**副产**（输入侧已在 walk 的停止分支里累加，不能重复计）
    import faults
    if not faults.enabled("boundary_no_byproduct"):
        for rid, slot in runs.items():
            for o, amt in slot["out"].items():
                if o in stop:
                    bump(gross_out, o, amt)

    return {"runs": runs, "gross_in": gross_in, "gross_out": gross_out,
            "target_direct": target_direct, "raw": raw, "trace": trace}


def derive_demand(ds: "Dataset", targets: Dict[str, float],
                  stop: Set[str], exclude_recipes: Set[str],
                  max_depth: int = 64):
    """
    兼容包装：返回 (净需求字典, 轨迹)。

    净需求 = 外部消耗 + 目标直接需求 − 外部副产。
    **不要**再用「只累计输入」的旧口径 —— 那会漏掉副产回供（见模块 docstring）。
    """
    ext = expand_external(ds, targets, stop, exclude_recipes, max_depth)
    demand: Dict[str, Fraction] = {}
    for k in set(ext["gross_in"]) | set(ext["gross_out"]) | set(ext["target_direct"]):
        demand[k] = (ext["gross_in"].get(k, Fraction(0))
                     + ext["target_direct"].get(k, Fraction(0))
                     - ext["gross_out"].get(k, Fraction(0)))
    return {k: float(v) for k, v in demand.items()}, ext["trace"]


def analyze(ds: "Dataset", scc_items: Sequence[str], targets: Dict[str, float],
            roundtrip_recipes: Optional[Set[str]] = None) -> Boundary:
    """
    给定一个物料生产循环（SCC），自动推导边界集合、入选配方与外部**净**需求。

    配方入选规则（两条，都可独立复核）:

      规则 A「环内配方」：该配方**同时消耗并产出** SCC 物品。
          例：x-ray-cracking 消耗氢气与精炼油、又产出氢气；
              reforming-refine 消耗氢气、产出精炼油。
          只「消耗」不「产出」SCC 物品的配方不算 —— 那种是环路的下游消费者
          （如卡西米尔晶体消耗 12 氢气），把它算进来会让边界无限膨胀。

      规则 B「必要供给者」：某个 SCC 物品既没有 mining 采集来源，
          又只有唯一一条非 mining 配方能产出它 —— 那条配方必然属于环路。
          例：精炼油没有矿脉配方，唯一非采集来源是 plasma-refining。

    两条规则合起来，本数据集恰好选出 {plasma-refining, reforming-refine, x-ray-cracking}，
    与案例 D 人工挑出的配方集完全一致。这是自动化正确性的第一个交叉验证点。

    被排除的配方会连同理由一起记录 —— 包括「产物越界」的那些
    （如 graphene-advanced 同时产石墨烯，石墨烯不由环路供应）。
    """
    roundtrip_recipes = roundtrip_recipes or set()
    b = Boundary(scc_items=sorted(scc_items), targets=dict(targets))
    inside = set(scc_items)

    # ---- 1) 规则 A：消耗 SCC 物品的配方 ----
    loop: Set[str] = set()
    for rid, r in ds.recipes.items():
        if rid in roundtrip_recipes:
            continue
        if "mining" in r.get("flags", []):
            continue
        if (set(r["in"]) & inside) and (set(r["out"]) & inside):
            loop.add(rid)

    # ---- 2) 规则 B：无采集来源的 SCC 物品的唯一非采集生产者 ----
    for item in inside:
        producers = [rid for rid in ds.produced_by.get(item, [])
                     if rid not in roundtrip_recipes
                     and "mining" not in ds.recipes[rid].get("flags", [])]
        has_mining = any("mining" in ds.recipes[rid].get("flags", [])
                         for rid in ds.produced_by.get(item, []))
        if not has_mining and producers:
            # 该物品没有任何采集来源，只能由这些配方供给 —— 它们必然属于环路
            loop |= set(producers)

    # ---- 3) 出边物品 = 环内配方的产物中不属于 SCC 的部分 ----
    outs: Set[str] = set()
    for rid in loop:
        outs |= set(ds.recipes[rid]["out"])
    b.outgoing = sorted(outs - inside)

    boundary_items = inside | set(b.outgoing)
    b.recipes = sorted(loop)

    # ---- 4) 记录被排除的配方及理由 ----
    for rid, r in sorted(ds.recipes.items()):
        if rid in loop:
            continue
        if rid in roundtrip_recipes:
            b.excluded_recipes.append((rid, "状态往返配方（净物料为零）"))
            continue
        if not (set(r["out"]) & (boundary_items | inside)):
            continue
        if "mining" in r.get("flags", []):
            b.excluded_recipes.append((rid, "mining 采集配方（属外购通道，不是生产列）"))
            continue
        extra = sorted(set(r["out"]) - boundary_items)
        if extra:
            b.excluded_recipes.append((rid, f"产物越界: {extra}"))
        else:
            b.excluded_recipes.append((rid, "不满足规则 A/B，未参与环路"))

    # ---- 5) 入边物品 ----
    inside_in: Set[str] = set()
    for rid in loop:
        inside_in |= set(ds.recipes[rid]["in"])
    b.incoming = sorted(inside_in - inside - set(b.outgoing))

    # ---- 6) 环外展开：记录实际运行速率、全部输入输出、按净额生成需求 ----
    b.rows = sorted(boundary_items)
    ext = expand_external(ds, targets, set(b.rows), set(b.recipes))
    b.trace = ext["trace"]
    b.external_runs = [
        ExternalRun(recipe=rid,
                    rate=slot["rate"],
                    inputs=dict(sorted(slot["in"].items())),
                    outputs=dict(sorted(slot["out"].items())),
                    kind="free" if not slot["in"] else "craft")
        for rid, slot in ext["runs"].items()
    ]
    b.gross_in = {k: float(v) for k, v in ext["gross_in"].items()}
    b.gross_out = {k: float(v) for k, v in ext["gross_out"].items()}
    b.target_direct = {k: float(v) for k, v in ext["target_direct"].items()}
    b.raw_inputs = {k: float(v) for k, v in ext["raw"].items()}

    for item in b.rows:
        net = (ext["gross_in"].get(item, Fraction(0))
               + ext["target_direct"].get(item, Fraction(0))
               - ext["gross_out"].get(item, Fraction(0)))
        b.demand[item] = float(net)
        if net < 0:
            # 外部副产超过外部消耗：必须显式记录，否则就是「静默丢弃」
            b.surplus[item] = float(-net)
    for r in b.rows:
        b.demand.setdefault(r, 0.0)

    return b


def net_matrix(ds: "Dataset", b: Boundary) -> List[List[Fraction]]:
    """
    净流量矩阵：A[i][j] = 配方 j 对物品 i 的（产出 - 消耗）。

    配方用量多为整数或简单小数，但个别值是 0.0000000000000017 这类浮点残差
    （如 deuterium 的副产比例）。直接 Fraction(float) 会得到 5764607.../5764607...
    这种天文数字分母，既难读又会在下游放大。这里先做有理近似再转 Fraction。
    """
    A = []
    for item in b.rows:
        row = []
        for rid in b.recipes:
            rec = ds.recipes[rid]
            row.append(_rat(rec["out"].get(item, 0)) - _rat(rec["in"].get(item, 0)))
        A.append(row)
    return A


def global_balance(ds: "Dataset", b: Boundary, targets: Dict[str, float],
                   loop_rates: Dict[str, float],
                   imports: Optional[Dict[str, float]] = None,
                   vents: Optional[Dict[str, float]] = None) -> Dict:
    """
    全局物料账：把**环外展开**与**环内 LP 解**拼成同一张表，逐物品核对残差。

        产出 = 环内净流量 + 环外副产 + 外购
        消耗 = 环外消耗 + 目标需求 + 放空
        残差 = 产出 − 消耗     （必须为 0）

    覆盖范围：
      * 环内 LP 的每一个平衡行；
      * 每一个目标物品；
      * 每条环外配方涉及的全部输入/输出物品（含既不进边界也不是目标的中间物，
        如临界光子 —— 它们若对不上，说明副产/中间物流有遗漏）。
    不覆盖：没有任何配方、被当作外部原料的物品（`b.raw_inputs`），
    它们由「外部采集」无限供给，不在本模型内闭合 —— 单独列在返回值里。

    返回 dict: rows（逐物品账目）, unbalanced（残差非零的物品）, raw（外部原料）,
               covered（计入校验的物品数）
    """
    imports = imports or {}
    vents = vents or {}
    rat = _rat
    row_idx = {it: i for i, it in enumerate(b.rows)}

    # 环外配方**全部**输入/输出（不只边界物品）—— 不这样算，能量矩阵、反物质
    # 这类「不进边界行、但由环外配方产出」的物品就会凭空少掉供给，
    # 全局账永远差一截。这是 v4 修掉的第二个局部性错误。
    ext_in: Dict[str, Fraction] = {}
    ext_out: Dict[str, Fraction] = {}
    for run in b.external_runs:
        for k, v in run.inputs.items():
            ext_in[k] = ext_in.get(k, Fraction(0)) + rat(v)
        for k, v in run.outputs.items():
            ext_out[k] = ext_out.get(k, Fraction(0)) + rat(v)

    items: Set[str] = set(b.rows) | set(targets)
    items |= set(ext_in) | set(ext_out)
    items -= set(b.raw_inputs)

    out: List[Dict] = []
    for item in sorted(items):
        loop_net = Fraction(0)
        if item in row_idx:
            i = row_idx[item]
            for j, rid in enumerate(b.recipes):
                rec = ds.recipes[rid]
                coeff = rat(rec["out"].get(item, 0)) - rat(rec["in"].get(item, 0))
                loop_net += coeff * rat(loop_rates.get(rid, 0))
        prod = loop_net + ext_out.get(item, Fraction(0)) + rat(imports.get(item, 0))
        cons = (ext_in.get(item, Fraction(0)) + rat(targets.get(item, 0))
                + rat(vents.get(item, 0)))
        out.append({"item": item, "loop": loop_net,
                    "ext_out": ext_out.get(item, Fraction(0)),
                    "import": rat(imports.get(item, 0)),
                    "ext_in": ext_in.get(item, Fraction(0)),
                    "target": rat(targets.get(item, 0)),
                    "vent": rat(vents.get(item, 0)),
                    "prod": prod, "cons": cons, "residual": prod - cons,
                    "is_row": item in row_idx})

    bad = [r for r in out if r["residual"] != 0]
    return {"rows": out, "unbalanced": bad, "raw": dict(b.raw_inputs),
            "covered": len(out)}


def describe(b: Boundary, ds: "Dataset") -> List[str]:
    out = []
    out.append(f"环内物品（SCC）      : {[ds.name(x) for x in b.scc_items]}")
    out.append(f"入边物品（外部供给）  : {[ds.name(x) for x in b.incoming]}")
    out.append(f"出边物品（对外供应）  : {[ds.name(x) for x in b.outgoing]}")
    out.append(f"平衡行               : {[ds.name(x) for x in b.rows]}")
    out.append(f"入选配方（{len(b.recipes)} 条）    : {b.recipes}")
    for rid, why in b.excluded_recipes:
        out.append(f"  排除 {rid:<28} 理由: {why}")
    out.append(f"环外展开配方（{len(b.external_runs)} 条，运行时速率与全部输入输出）:")
    for run in b.external_runs:
        ins = ", ".join(f"{ds.name(k)}×{float(v):.4f}" for k, v in run.inputs.items()) or "—（无物料输入）"
        outs = ", ".join(f"{ds.name(k)}×{float(v):.4f}" for k, v in run.outputs.items())
        out.append(f"  {run.recipe:<24} ×{float(run.rate):8.4f}/s   {ins}  →  {outs}")
    if b.raw_inputs:
        out.append("外部原料（无配方，不计入闭合校验）: "
                   + ", ".join(f"{ds.name(k)} {v:.4f}/s" for k, v in b.raw_inputs.items()))
    out.append("边界物品净需求（外部消耗 + 目标直接 − 外部副产）:")
    for item in b.rows:
        gi, go = b.gross_in.get(item, 0.0), b.gross_out.get(item, 0.0)
        td = b.target_direct.get(item, 0.0)
        out.append(f"  {ds.name(item):<20} {gi:8.4f} + {td:8.4f} − {go:8.4f}"
                   f" = {b.demand[item]:8.4f}/s")
    if b.surplus:
        out.append("⚠ 外部副产超过外部消耗，须由环内吸收或放空: "
                   + ", ".join(f"{ds.name(k)} {v:.4f}/s" for k, v in b.surplus.items()))
    return out
