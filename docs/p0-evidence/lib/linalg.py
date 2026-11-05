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
      status : "optimal" | "infeasible" | "unbounded" | "maxiter"
      x      : List[Fraction]（original 变量，长度 = len(c)）；仅 optimal 时非 None
      z      : Fraction 目标值；仅 optimal 时非 None
      iters  : 迭代次数

    ⚠ "maxiter" 是第四种状态，**不是** infeasible、也**不是** optimal。
      它是「迭代预算耗尽、结论未知」。两阶段任一阶段命中都必须原样传出：
        - 若把它当 infeasible，会把一个可能可行的问题误报为无解；
        - 若把它当 optimal，会返回一个未证明最优的可行解（甚至目标值更大）。
      调用方必须显式处理该状态，见 case_f 的 F7/F8 回归用例。
    """
    import faults
    if faults.enabled("lp_all_infeasible"):
        # 故障注入：让求解器一律报不可行。放在最底层，使所有调用方
        # （lpio.solve_self、案例 G/H 的字典序求解）都受影响 ——
        # 放在某一层调用方里，其他路径就会绕过注入，自检因此失去意义。
        return {"status": "infeasible", "x": None, "z": None, "iters": 0}

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
        """
        迭代预算按**枢轴次数**计，而不是「循环次数」。

        原因: 循环体最后一次进入时通常只是「检查是否还有可入基的列」，
        它不改变解，却会被旧实现计入迭代数，导致 max_iter=N 时明明已到最优
        却被判为 maxiter。预算应当只约束真正改变解的枢轴操作。
        """
        iters = iters_start
        while True:
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
            if iters >= max_iter:                 # 还要再枢轴一次，但预算已耗尽
                return "maxiter", iters
            pivot(leave, enter)
            basis[leave] = enter
            iters += 1

    # ---- Phase 1: 最小化人工变量之和 ----
    c1 = [Fraction(0)] * n + [Fraction(1)] * m
    st, iters = run(c1, list(range(total)), 0)
    if st == "maxiter":
        # 迭代预算耗尽：第一阶段尚未得出结论，不能冒充 infeasible
        return {"status": "maxiter", "x": None, "z": None, "iters": iters,
                "phase": 1, "reason": f"第一阶段迭代超过 max_iter={max_iter}"}
    if st != "optimal":
        return {"status": "unbounded" if st == "unbounded" else "infeasible",
                "x": None, "z": None, "iters": iters, "phase": 1}

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
    if st == "maxiter":
        # 第二阶段预算耗尽：当前基可行但未证明最优，禁止报 optimal
        return {"status": "maxiter", "x": None, "z": None, "iters": iters,
                "phase": 2, "reason": f"第二阶段迭代超过 max_iter={max_iter}"}
    if st == "unbounded":
        return {"status": "unbounded", "x": None, "z": None, "iters": iters, "phase": 2}

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


# --------------------------------------------------------------------------- #
# 独立最优值基准：穷举基可行解（与单纯形不同源的算法）
# --------------------------------------------------------------------------- #
def _rank_of_rows(rows: Sequence[Sequence[Fraction]]) -> int:
    """一组行向量的秩（增广一列 0，复用同一套 RREF）。"""
    if not rows:
        return 0
    n = len(rows[0])
    aug = [list(r) + [Fraction(0)] for r in rows]
    _, _, r = _rref(aug)
    return r


def independent_rows(A: Sequence[Sequence[float]]) -> Tuple[List[int], int]:
    """
    贪心选出**线性无关的原始行索引**，并返回行秩。

    为什么不能直接用 m 行：等式约束里经常出现重复行或线性组合行
    （例如同一个配方既被写成「每台产量」又被写成「整厂产量」）。
    用 m 行去枚举 m 列基，会把「矩阵秩亏」误当成「没有可行极点」。
    """
    Af = [[_F(v) for v in row] for row in A]
    chosen: List[int] = []
    rank = 0
    for i in range(len(Af)):
        trial = [Af[j] for j in chosen] + [Af[i]]
        r = _rank_of_rows(trial)
        if r > rank:
            chosen.append(i)
            rank = r
    return chosen, rank


def enumerate_optimum(c: Sequence[float], A: Sequence[Sequence[float]],
                      b: Sequence[float], max_bases: int = 200000) -> Dict:
    """
    穷举基可行解（BFS），精确求解 B·x_B = b，保留 x_B >= 0 者，取目标值最小者。

    用途: 给单纯形提供一个**算法上不同源**的对照基准 —— 它不做枢轴、不做比值检验，
    只做组合枚举 + 精确高斯消元。两者在最优值上一致，可以排除「同一个 bug 在两边都犯」。

    与 v3 的区别（v4 修正）
    ----------------------
    v3 直接按**原始行数 m** 选 m 列。遇到重复约束就必然枚举失败：

        min x   s.t.  x = 1 ;  x = 1 ;  x >= 0      # 正确答案 x=1, z=1
        v3: 两行一列，没有 C(1,2) 这种组合 → status="no_vertices"

    而 `no_vertices` 当时被调用方当作「不可行」的证据 —— 一个可行的模型被判死。
    现在的顺序是：

      1. 先对增广矩阵 [A|b] 做 RREF，检查**一致性**；有 0=c(c≠0) 行即判 infeasible；
      2. 再用 `independent_rows()` 取有效行秩 r（丢掉冗余行），按 r 列枚举；
      3. 一致但枚举不到 BFS 时，才判 infeasible ——
         这一步是有定理支撑的：{x>=0 : Ax=b} 非空 ⇒ 必存在基可行解
         （取支撑集最小的可行点，其正分量对应的列必线性无关）。

    返回 status:
      "has_vertices" : 至少一个 BFS；z_best 为其中最小目标值（目标有界时即最优值）
      "infeasible"   : 已证明可行域为空。reason ∈ {"rref_contradiction", "no_bfs"}
      "too_large"    : 规模超限，**未枚举**（结论未知，不得当作不可行）
      "unsupported"  : 结构上不支持（空模型 / 行秩为 0）
    """
    from itertools import combinations

    m = len(A)
    n = len(c)
    if m == 0 or n == 0:
        return {"status": "unsupported", "reason": "空模型", "z_best": None,
                "x_best": None, "n_feasible": 0, "rank": 0, "rows_used": []}

    cf = [_F(v) for v in c]
    Af = [[_F(v) for v in row] for row in A]
    bf = [_F(v) for v in b]

    # ---- 1) 一致性：增广矩阵 RREF，0 = 非零 即矛盾 ----
    aug = [Af[i] + [bf[i]] for i in range(m)]
    R, _piv, _rk = _rref([row[:] for row in aug])
    if any(all(v == 0 for v in row[:n]) and row[n] != 0 for row in R):
        return {"status": "infeasible", "reason": "rref_contradiction",
                "z_best": None, "x_best": None, "n_feasible": 0,
                "rank": _rank_of_rows(Af), "rows_used": []}

    # ---- 2) 有效行秩（丢掉重复/线性相关的约束行）----
    rows_used, r = independent_rows(Af)
    import faults
    if faults.enabled("enumerate_raw_rows"):
        # 故障注入：退回 v3 的「按原始行数选列」，重复约束下必然枚举失败
        rows_used, r = list(range(len(Af))), len(Af)
    if r == 0:
        return {"status": "unsupported", "reason": "行秩为 0（约束全为 0=0）",
                "z_best": None, "x_best": None, "n_feasible": 0,
                "rank": 0, "rows_used": []}
    if r > 5 or n > 24:
        return {"status": "too_large", "reason": f"规模超限 rank={r}, n={n}",
                "z_best": None, "x_best": None, "n_feasible": 0,
                "rank": r, "rows_used": rows_used}

    total = 1
    for k in range(r):
        total = total * (n - k) // (k + 1)
    if total > max_bases:
        return {"status": "too_large", "reason": f"组合数 {total} > {max_bases}",
                "z_best": None, "x_best": None, "n_feasible": 0,
                "rank": r, "rows_used": rows_used}

    Ar = [Af[i] for i in rows_used]
    br = [bf[i] for i in rows_used]

    def solve_square(M, rhs):
        k = len(M)
        aug2 = [list(M[i]) + [rhs[i]] for i in range(k)]
        for col in range(k):
            piv = None
            for rr in range(col, k):
                if aug2[rr][col] != 0:
                    piv = rr
                    break
            if piv is None:
                return None
            aug2[col], aug2[piv] = aug2[piv], aug2[col]
            pv = aug2[col][col]
            aug2[col] = [v / pv for v in aug2[col]]
            for rr in range(k):
                if rr != col and aug2[rr][col] != 0:
                    f = aug2[rr][col]
                    aug2[rr] = [aug2[rr][t] - f * aug2[col][t] for t in range(k + 1)]
        return [aug2[i][k] for i in range(k)]

    z_best = None
    x_best = None
    n_feasible = 0
    for cols in combinations(range(n), r):
        B = [[Ar[i][j] for j in cols] for i in range(r)]
        sol = solve_square(B, br)
        if sol is None:
            continue
        if any(v < 0 for v in sol):
            continue
        n_feasible += 1
        x = [Fraction(0)] * n
        for j, v in zip(cols, sol):
            x[j] = v
        z = sum(cf[j] * x[j] for j in range(n))
        if z_best is None or z < z_best:
            z_best = z
            x_best = x

    base = {"rank": r, "rows_used": rows_used, "n_bases": total,
            "n_feasible": n_feasible}
    if n_feasible == 0:
        # 一致 + 无 BFS ⇒ 可行域为空（非空多面体必有基可行解）
        return dict(base, status="infeasible", reason="no_bfs",
                    z_best=None, x_best=None)
    return dict(base, status="has_vertices", reason="", z_best=z_best, x_best=x_best)


# --------------------------------------------------------------------------- #
# 无界性证书：必须是「可行点 + 保持等式的下降方向」，不能只看单列系数
# --------------------------------------------------------------------------- #
def legacy_column_ray_unsound(c: Sequence[float], A: Sequence[Sequence[float]]) -> bool:
    """
    v3 用过的**错误**判据（保留仅为在回归中演示它会误报，任何求解路径都不得调用）。

        存在列 j 使 c_j < 0 且 A[:,j] <= 0  ⇒  无界

    模型是等式约束 A·x = b，可行方向必须满足 **A·d = 0**，而不是 A·d <= 0。
    反例（有界却会被本判据判为无界）:

        min -x   s.t.  -x = -1,  x >= 0        # 唯一解 x=1, z=-1

    列 x 的成本 -1 < 0，系数 -1 <= 0，判据返回 True；但沿 e_x 走一步就破坏等式。
    """
    m = len(A)
    n = len(c)
    for j in range(n):
        if _F(c[j]) < 0 and all(_F(A[i][j]) <= 0 for i in range(m)):
            return True
    return False


def verify_ray_certificate(c: Sequence[float], A: Sequence[Sequence[float]],
                           b: Sequence[float], x0: Sequence[float],
                           d: Sequence[float],
                           t_samples: Sequence[float] = (0, 1, 10, 1000)) -> Dict:
    """
    逐项代数验证「无界性证书」，不依赖任何求解器。

    证书 = (x0, d)，必须同时满足：

      1. A·x0 = b        —— x0 是可行点
      2. x0 >= 0
      3. A·d  = 0        —— 方向保持等式（这是 v3 漏掉的那一条）
      4. d >= 0          —— 方向不离开非负象限
      5. d != 0          —— 否则不是方向
      6. c·d < 0         —— 目标沿方向严格下降

    1–6 成立时，x(t) = x0 + t·d 对任意 t >= 0 都可行，且 z(t) = c·x0 + t·(c·d) → -∞。
    额外用若干 t 采样把这条推论也验一遍（防御 3/4 的实现写错）。

    注意：本函数**不判断问题是否可行**——它要求调用方给出 x0。
    只验证方向、不给 x0，就不能宣称整个问题无界（不可行问题也可能有下降方向）。
    """
    n = len(c)
    if len(x0) != n or len(d) != n:
        return {"ok": False, "checks": [{"name": "维度", "ok": False,
                                         "detail": f"x0/d 长度应为 {n}"}],
                "cd": None, "samples": []}
    x0f = [_F(v) for v in x0]
    df = [_F(v) for v in d]
    checks: List[Dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for i, row in enumerate(A):
        lhs = sum(_F(row[j]) * x0f[j] for j in range(n))
        add(f"A·x0 = b（第 {i} 行）", lhs == _F(b[i]), f"{lhs} vs {b[i]}")
    add("x0 >= 0", all(v >= 0 for v in x0f),
        "[" + ", ".join(str(v) for v in x0f) + "]")

    for i, row in enumerate(A):
        lhs = sum(_F(row[j]) * df[j] for j in range(n))
        add(f"A·d = 0（第 {i} 行）", lhs == 0, f"{lhs} vs 0")
    add("d >= 0", all(v >= 0 for v in df),
        "[" + ", ".join(str(v) for v in df) + "]")
    add("d != 0", any(v != 0 for v in df), "方向非零")

    cd = sum(_F(c[j]) * df[j] for j in range(n))
    add("c·d < 0", cd < 0, f"c·d = {cd}")

    z0 = sum(_F(c[j]) * x0f[j] for j in range(n))
    samples = []
    ok_samples = True
    for t in t_samples:
        tf = _F(t)
        xt = [x0f[j] + tf * df[j] for j in range(n)]
        row_res = [sum(_F(A[i][j]) * xt[j] for j in range(n)) - _F(b[i])
                   for i in range(len(A))]
        feas = all(v == 0 for v in row_res) and all(v >= 0 for v in xt)
        zt = z0 + tf * cd
        dec = (t == 0) or (zt < z0)
        ok_samples &= feas and dec
        samples.append({"t": t, "x": xt, "z": zt, "feasible": feas, "decreasing": dec})
    add("沿方向采样：x0+t·d 可行且目标严格下降", ok_samples,
        "; ".join(f"t={s['t']}: z={s['z']}" for s in samples))

    return {"ok": all(ch["ok"] for ch in checks), "checks": checks,
            "cd": cd, "z0": z0, "samples": samples, "x0": x0f, "d": df}


def prove_unbounded(c: Sequence[float], A: Sequence[Sequence[float]],
                    b: Sequence[float], max_iter: int = 5000) -> Dict:
    """
    判定 min c·x, A·x=b, x>=0 是「无界」「有界」还是「不可行」，并给出可复算的证书。

    LP 理论：可行 + 存在下降的回收方向（A·d=0, d>=0, c·d<0） ⇔ 无界。
    因此本函数分两步，**先可行性、再方向**：

      1. 解 min 0 s.t. A·x=b, x>=0 得到 x0。infeasible ⇒ 直接返回 infeasible
         —— 不可行问题既不是有界也不是无界，绝不能报 unbounded。
      2. 解 min c·d s.t. A·d=0, Σd=1, d>=0（Σd=1 只是归一化，排除 d=0）：
           infeasible          ⇒ 非负零空间只有零向量 ⇒ 无射线 ⇒ bounded
           optimal 且 c·d >= 0 ⇒ 所有可行方向都不下降     ⇒ bounded
           optimal 且 c·d <  0 ⇒ 得到显式 d，交给 verify_ray_certificate 逐项复核

    返回 status: "unbounded" | "bounded" | "infeasible" | "unknown" | "certificate_failed"
    `reason` 说明依据；unbounded 时附 `certificate`（含 x0/d 与逐项检查）。
    """
    n = len(c)
    import faults
    if faults.enabled("ray_column_only"):
        # 故障注入：退回 v3 的「单列系数」判据，不验可行性、不验 A·d=0
        return {"status": "unbounded" if legacy_column_ray_unsound(c, A) else "bounded",
                "reason": "故障注入：仅按单列系数判定"}
    r0 = simplex_min([Fraction(0)] * n, A, b, max_iter=max_iter)
    if r0["status"] == "infeasible":
        return {"status": "infeasible", "reason": "存在可行点这一前提不成立"}
    if r0["status"] != "optimal":
        return {"status": "unknown", "reason": f"可行性判定未完成: {r0['status']}"}
    x0 = r0["x"]

    A2 = [list(row) for row in A] + [[Fraction(1)] * n]
    b2 = [Fraction(0)] * len(A) + [Fraction(1)]
    rd = simplex_min(list(c), A2, b2, max_iter=max_iter)
    if rd["status"] == "infeasible":
        return {"status": "bounded", "x0": x0,
                "reason": "不存在非零的非负零空间方向（A·d=0, d>=0 只有 d=0）"}
    if rd["status"] != "optimal":
        return {"status": "unknown", "reason": f"方向判定未完成: {rd['status']}"}

    d = rd["x"]
    cd = sum(_F(c[j]) * d[j] for j in range(n))
    if cd >= 0:
        return {"status": "bounded", "x0": x0, "d": d, "cd": cd,
                "reason": f"最优可行方向 c·d = {cd} >= 0，不存在下降方向"}
    cert = verify_ray_certificate(c, A, b, x0, d)
    if not cert["ok"]:
        return {"status": "certificate_failed", "certificate": cert,
                "reason": "构造出的方向未通过逐项验证"}
    return {"status": "unbounded", "certificate": cert, "cd": cd,
            "reason": "x0 可行 + A·d=0, d>=0, c·d<0"}
