#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dyson Canvas P0 —— 线性方程组分类与标准形单纯形（零依赖、Fraction 精确算术）

为什么用 Fraction 而不是 float:
  FactorioLab 用 rational（bigint 分数）做全部求解运算。本文件用同样的思路，
  目的是让「负速率」「奇异」「欠定」这类判定不受浮点舍入影响 —— 否则 -1e-16 会
  被误读成负速率，或 1e-17 被误读成非零主元。这是可直接验证的工程收益。

提供:
  classify(A, b)        —— 高斯消元 + 分类（唯一解 / 无穷多解 / 无解），含秩与零空间
  simplex_min(c, A, b)  —— 标准形 min c·x, s.t. A·x = b, x >= 0（两阶段法 + Bland 规则）
"""
from __future__ import annotations

from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple


def _F(x) -> Fraction:
    if isinstance(x, Fraction):
        return x
    if isinstance(x, int):
        return Fraction(x)
    return Fraction(x).limit_denominator(10 ** 9)


def _rref(aug: List[List[Fraction]]) -> Tuple[List[List[Fraction]], List[int], int]:
    """对增广矩阵做 RREF。返回 (矩阵, 主元列列表, 秩)。"""
    m = len(aug)
    n = len(aug[0]) - 1
    A = [row[:] for row in aug]
    pivots: List[int] = []
    r = 0
    for c in range(n):
        # 选主元：取该列绝对值最大的行（部分选主元）
        p = None
        best = Fraction(0)
        for i in range(r, m):
            if abs(A[i][c]) > best:
                best = abs(A[i][c])
                p = i
        if p is None:
            continue
        A[r], A[p] = A[p], A[r]
        pv = A[r][c]
        A[r] = [v / pv for v in A[r]]
        for i in range(m):
            if i != r and A[i][c] != 0:
                f = A[i][c]
                A[i] = [A[i][k] - f * A[r][k] for k in range(n + 1)]
        pivots.append(c)
        r += 1
        if r == m:
            break
    return A, pivots, r


def classify(A: Sequence[Sequence[float]], b: Sequence[float]) -> Dict:
    """
    判定 A·x = b。

    返回 dict:
      status   : "unique" | "infinite" | "inconsistent"
      rank_a, rank_aug, n
      solution : List[Fraction]（unique / infinite 时给出一个特解，自由变量取 0）
      nullspace: List[List[Fraction]]（infinite 时的零空间基）
      free_vars: List[int]
    """
    m = len(A)
    n = len(A[0]) if m else 0
    aug = [[_F(A[i][j]) for j in range(n)] + [_F(b[i])] for i in range(m)]
    R, pivots, rank_a = _rref([[row[j] for j in range(n)] + [row[n]] for row in aug])

    # 增广矩阵的秩：RREF 后若存在 0=1 行，则不相容
    inconsistent = False
    for row in R:
        if all(v == 0 for v in row[:n]) and row[n] != 0:
            inconsistent = True
            break
    rank_aug = rank_a if not inconsistent else rank_a + 1

    if inconsistent:
        return {"status": "inconsistent", "rank_a": rank_a, "rank_aug": rank_aug,
                "n": n, "solution": None, "nullspace": [], "free_vars": []}

    free_vars = [c for c in range(n) if c not in pivots]
    sol = [Fraction(0)] * n
    for i, pc in enumerate(pivots):
        sol[pc] = R[i][n]

    nullspace: List[List[Fraction]] = []
    for f in free_vars:
        vec = [Fraction(0)] * n
        vec[f] = Fraction(1)
        for i, pc in enumerate(pivots):
            vec[pc] = -R[i][f]
        nullspace.append(vec)

    status = "unique" if rank_a == n else "infinite"
    return {"status": status, "rank_a": rank_a, "rank_aug": rank_aug, "n": n,
            "solution": sol, "nullspace": nullspace, "free_vars": free_vars}


# --------------------------------------------------------------------------- #
# 标准形单纯形: min c·x  s.t.  A·x = b,  x >= 0
# --------------------------------------------------------------------------- #
def simplex_min(c: Sequence[float], A: Sequence[Sequence[float]],
                b: Sequence[float], max_iter: int = 5000) -> Dict:
    """
    两阶段法 + Bland 规则（防循环），全程 Fraction 精确算术。

    返回 dict:
      status : "optimal" | "infeasible" | "unbounded"
      x      : List[Fraction]（original 变量，长度 = len(c)）
      z      : Fraction 目标值
      iters  : 迭代次数
    """
    m = len(A)
    n = len(c)
    cf = [_F(v) for v in c]

    # 统一 b >= 0（整行乘 -1，不改变可行域）
    Af: List[List[Fraction]] = []
    bf: List[Fraction] = []
    for i in range(m):
        row = [_F(v) for v in A[i]]
        bi = _F(b[i])
        if bi < 0:
            row = [-v for v in row]
            bi = -bi
        Af.append(row)
        bf.append(bi)

    total = n + m  # 原变量 + 人工变量
    T: List[List[Fraction]] = []
    for i in range(m):
        T.append(Af[i] + [Fraction(1) if j == i else Fraction(0) for j in range(m)] + [bf[i]])
    basis = [n + i for i in range(m)]

    def pivot(row_idx: int, col_idx: int) -> None:
        pv = T[row_idx][col_idx]
        T[row_idx] = [v / pv for v in T[row_idx]]
        for i in range(m):
            if i != row_idx and T[i][col_idx] != 0:
                f = T[i][col_idx]
                T[i] = [T[i][k] - f * T[row_idx][k] for k in range(total + 1)]

    def objective_row(cost: List[Fraction]) -> List[Fraction]:
        # 返回 reduced cost 行：c_j - z_j（z_j = c_B^T B^-1 A_j）。
        # 最小化问题中 c_j - z_j < 0 表示增加 x_j 可降低目标值。
        # 末位保存当前目标值 z，仅供调试。
        z = [Fraction(0)] * (total + 1)
        for i in range(m):
            cb = cost[basis[i]]
            if cb == 0:
                continue
            for k in range(total + 1):
                z[k] += cb * T[i][k]
        red = [cost[k] - z[k] for k in range(total)] + [z[total]]
        return red

    def run(cost: List[Fraction], allowed: List[int], iters_start: int) -> Tuple[str, int]:
        iters = iters_start
        while True:
            iters += 1
            if iters > max_iter:
                return "maxiter", iters
            red = objective_row(cost)
            enter = None
            for j in allowed:                     # Bland 规则：取最小下标
                if red[j] < 0:
                    enter = j
                    break
            if enter is None:
                return "optimal", iters
            # 比值检验
            leave = None
            best_ratio = None
            for i in range(m):
                aij = T[i][enter]
                if aij > 0:
                    ratio = T[i][total] / aij
                    if best_ratio is None or ratio < best_ratio or (
                            ratio == best_ratio and basis[i] < basis[leave]):
                        best_ratio = ratio
                        leave = i
            if leave is None:
                return "unbounded", iters
            pivot(leave, enter)
            basis[leave] = enter

    # ---- Phase 1: 最小化人工变量之和 ----
    c1 = [Fraction(0)] * n + [Fraction(1)] * m
    st, iters = run(c1, list(range(total)), 0)
    if st != "optimal":
        return {"status": "unbounded" if st == "unbounded" else "infeasible",
                "x": None, "z": None, "iters": iters}

    artificial_sum = sum(T[i][total] for i in range(m) if basis[i] >= n)
    if artificial_sum > 0:
        return {"status": "infeasible", "x": None, "z": None, "iters": iters}

    # 把仍在基中的人工变量换出（若该行原变量全为 0，则该行是冗余约束，保留但无影响）
    for i in range(m):
        if basis[i] >= n:
            for j in range(n):
                if T[i][j] != 0:
                    pivot(i, j)
                    basis[i] = j
                    break

    # ---- Phase 2: 原目标 ----
    c2 = cf + [Fraction(0)] * m
    st, iters = run(c2, list(range(n)), iters)
    if st == "unbounded":
        return {"status": "unbounded", "x": None, "z": None, "iters": iters}

    x = [Fraction(0)] * n
    for i in range(m):
        if basis[i] < n:
            x[basis[i]] = T[i][total]
    z = sum(cf[j] * x[j] for j in range(n))
    return {"status": "optimal", "x": x, "z": z, "iters": iters}


# --------------------------------------------------------------------------- #
# 字典序多目标（sequential / preemptive optimization）
# --------------------------------------------------------------------------- #
def lexicographic_min(layers, A, b, max_iter: int = 5000) -> Dict:
    """
    按给定优先级逐层最小化：第 k 层求解后，把其最优值作为等式约束固定下来，
    再解第 k+1 层。这样优先级是**严格的**，不依赖权重的数量级标定。

    layers: [(cost_vector, label), ...]，列表顺序即优先级（前者优先）

    返回:
      status : "optimal" | "infeasible" | "unbounded"
      x      : 最终解
      stages : [{label, z}, ...] 每层的最优值
    """
    A_aug = [list(row) for row in A]
    b_aug = list(b)
    stages = []
    x = None
    for k, (cost, label) in enumerate(layers):
        r = simplex_min(cost, A_aug, b_aug, max_iter=max_iter)
        if r["status"] != "optimal":
            return {"status": r["status"], "x": None, "z": None,
                    "stages": stages, "failed_at": label}
        stages.append({"label": label, "z": r["z"]})
        # 把本层最优值锁死，交给下一层
        A_aug.append(list(cost))
        b_aug.append(r["z"])
        x = r["x"]
    return {"status": "optimal", "x": x,
            "z": sum(stages[-1]["z"] for _ in [0]) if stages else None,
            "stages": stages}
