#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LP 模型 I/O 与 GLPK 对照工具

用途:
  把同一组线性规划模型分别交给
    (a) 本仓库自实现的 `linalg.simplex_min`（Fraction 精确算术 + Bland 规则）
    (b) GLPK 5.0（glpsol，业界标准实现）
  求解并逐项比对，用于回答「自研求解器是否可信」。

设计要点:
  * 两侧拿到的模型描述是**同一份数据**（LPProblem），杜绝人工转录误差。
  * 除互相比对外，另做一次**独立的可行性校验**：把任一方给出的 x 代回原模型，
    检查等式是否成立、变量是否非负。这样即使两边都错也能被发现。
  * GLPK 未安装时，比对自动降级为「仅自实现 + 独立校验」，并在输出中明确标注，
    不允许把「未对照」写成「已对照」。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

GLPSOL_CANDIDATES = [
    "/tmp/glpk/bin/glpsol",
    shutil.which("glpsol") or "",
]


def find_glpsol() -> Optional[str]:
    for p in GLPSOL_CANDIDATES:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


@dataclass
class LPProblem:
    """标准形: min c·x  s.t.  A·x = b,  x >= 0"""
    name: str
    c: Sequence[float]
    A: Sequence[Sequence[float]]
    b: Sequence[float]
    col_names: List[str] = field(default_factory=list)
    row_names: List[str] = field(default_factory=list)
    note: str = ""

    def __post_init__(self):
        n = len(self.c)
        if not self.col_names:
            self.col_names = [f"x{j+1}" for j in range(n)]
        if not self.row_names:
            self.row_names = [f"c{i+1}" for i in range(len(self.A))]


# --------------------------------------------------------------------------- #
# LP 文件写出（CPLEX LP 格式，glpsol --lp 可直接读）
# --------------------------------------------------------------------------- #
def _num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.12g}"


def _expr(coefs: Sequence[float], names: Sequence[str]) -> str:
    parts = []
    for v, nm in zip(coefs, names):
        if v == 0:
            continue
        sign = "-" if v < 0 else "+"
        mag = abs(v)
        parts.append(f" {sign} {_num(mag)} {nm}")
    if not parts:
        return " 0 dummy0"
    return "".join(parts)


def write_lp(path: str, p: LPProblem) -> None:
    lines = ["Minimize", f" obj:{_expr(p.c, p.col_names)}", "Subject To"]
    for i, row in enumerate(p.A):
        lines.append(f" {p.row_names[i]}:{_expr(row, p.col_names)} = {_num(p.b[i])}")
    lines.append("Bounds")
    for nm in p.col_names:
        lines.append(f" {nm} >= 0")
    lines.append("End")
    # LP 格式要求每行列数受限，这里模型都很小，直接写
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# 两侧求解
# --------------------------------------------------------------------------- #
def solve_self(p: LPProblem, max_iter: int = 5000) -> Dict:
    """
    调用本仓库自实现的单纯形。

    max_iter 可显式调小以构造「迭代预算耗尽」场景（见 case_f 的 F7/F8）。
    status == "maxiter" 时 x/z 必为 None —— 未完成的求解不提供解，避免调用方误用。
    """
    import linalg
    import faults
    if faults.enabled("lp_maxiter"):
        max_iter = 1
    # 注: lp_all_infeasible 的注入点在 linalg.simplex_min 内部（最底层），
    #     这样所有调用路径都会被覆盖，而不是只有经过本函数的那条。
    r = linalg.simplex_min(list(p.c), [list(row) for row in p.A], list(p.b),
                           max_iter=max_iter)
    return {
        "solver": "self",
        "status": r["status"],
        "x": [float(v) for v in r["x"]] if r["x"] else None,
        "z": float(r["z"]) if r["z"] is not None else None,
        "iters": r.get("iters"),
        "phase": r.get("phase"),
        "reason": r.get("reason"),
    }


_STATUS_MAP = {
    "OPTIMAL": "optimal",
    "INFEASIBLE": "infeasible",
    "UNBOUNDED": "unbounded",
    "FEASIBLE": "feasible",
    # UNDEFINED 是 GLPK 的「结论未知」状态，既可能是无界也可能不可行，
    # 必须靠 stdout 消歧（见 solve_glpk）。默认映射到 unknown 而不是 infeasible，
    # 因为把未知说成不可行，会误导用户去改配方。
    "UNDEFINED": "unknown",
}


def solve_glpk(p: LPProblem, glpsol: Optional[str] = None) -> Dict:
    glpsol = glpsol or find_glpsol()
    if not glpsol:
        return {"solver": "glpk", "status": "unavailable", "x": None, "z": None}

    tmpdir = tempfile.mkdtemp(prefix="lpcmp-")
    lp_path = os.path.join(tmpdir, "m.lp")
    sol_path = os.path.join(tmpdir, "m.sol")
    write_lp(lp_path, p)
    try:
        # 必须加 --nopresol：GLPK 预解器在判定无界时会把状态写成 UNDEFINED，
        # 与「不可行」混淆。关掉预解后 stdout 会明确给出 UNBOUNDED PRIMAL。
        proc = subprocess.run([glpsol, "--lp", lp_path, "--nopresol", "-o", sol_path],
                              check=False, capture_output=True, timeout=60)
        log = (proc.stdout or b"").decode("utf-8", "replace")
        if not os.path.exists(sol_path):
            return {"solver": "glpk", "status": "error", "x": None, "z": None}
        txt = open(sol_path, encoding="utf-8", errors="replace").read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    m_status = re.search(r"^Status:\s+(\S+)", txt, re.M)
    raw = m_status.group(1) if m_status else "UNKNOWN"
    status = _STATUS_MAP.get(raw, raw.lower())
    # UNDEFINED 需要靠 stdout 消歧
    if raw == "UNDEFINED":
        if "UNBOUNDED PRIMAL" in log:
            status = "unbounded"
        elif "NO PRIMAL FEASIBLE" in log:
            status = "infeasible"
    return _finish(txt, status, raw, p)


def _finish(txt: str, status: str, raw: str, p: "LPProblem") -> Dict:

    z = None
    m_obj = re.search(r"^Objective:\s+\S+\s*=\s*([-+0-9.eE]+)", txt, re.M)
    if m_obj:
        z = float(m_obj.group(1))

    x = None
    if status == "optimal":
        # 列区块：No. Column name St Activity ...
        block = txt.split("Column name")
        if len(block) > 1:
            body = block[1].split("Karush")[0]
            vals: Dict[str, float] = {}
            for line in body.splitlines():
                m = re.match(r"\s*\d+\s+(\S+)\s+\S+\s+([-+0-9.eE]+)", line)
                if m:
                    vals[m.group(1)] = float(m.group(2))
            x = [vals.get(nm, 0.0) for nm in p.col_names]
    return {"solver": "glpk", "status": status, "raw_status": raw, "x": x, "z": z}


# --------------------------------------------------------------------------- #
# 独立可行性校验（不依赖任何求解器）
# --------------------------------------------------------------------------- #
def verify_feasible(p: LPProblem, x: Sequence[float], tol: float = 1e-6) -> Tuple[bool, List[str]]:
    """把解代回原模型：等式残差 + 非负性。"""
    errs: List[str] = []
    for i, row in enumerate(p.A):
        lhs = sum(row[j] * x[j] for j in range(len(x)))
        if abs(lhs - p.b[i]) > tol:
            errs.append(f"row {p.row_names[i]}: {lhs:.9f} != {p.b[i]:.9f}")
    for j, v in enumerate(x):
        if v < -tol:
            errs.append(f"col {p.col_names[j]}: {v:.9f} < 0")
    return (len(errs) == 0), errs


def objective(p: LPProblem, x: Sequence[float]) -> float:
    return sum(p.c[j] * x[j] for j in range(len(x)))
