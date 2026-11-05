# v4 反例复现（修复前）

本目录保存**第三轮复验**给出的三个反例在**修改代码之前**的原始输出。
它们是「问题确实存在」的证据，不是当前行为 —— 修复后的预期结果见每节末尾。

运行环境：Python 3 + `/tmp/glpk/bin/glpsol`（GLPK 5.0），DSP 数据 `0.10.29.21950`。

## r1_ray_pre_fix.log —— 负射线判据在有界模型上误报

模型：

```text
min -x
-x = -1
x >= 0
```

唯一解 `x=1`、最优值 `-1`，显然有界。

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "lib")
import linalg
print(linalg.has_negative_ray([-1], [[-1]]))      # 修复前：True（误报）
PY
```

修复后：

```python
linalg.prove_unbounded([-1], [[-1]], [-1])["status"]   # "bounded"
```

## r2_enum_pre_fix.log —— 顶点枚举在冗余约束下失败

模型：

```text
min x
x = 1
x = 1
x >= 0
```

正确答案 `x=1, z=1`。

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "lib")
import linalg
print(linalg.enumerate_optimum([1], [[1], [1]], [1, 1]))   # 修复前：no_vertices
PY
```

修复后：

```python
linalg.enumerate_optimum([1], [[1], [1]], [1, 1])["status"]   # "has_vertices"
```

## r3_byproduct_pre_fix.log —— 环外副产氢气未回供

目标：能量矩阵 2/s + 反物质 1/s。

```text
energy-matrix       ×2.0/s : 2 EG + 2 H2 → 1 能量矩阵      ⇒ 耗 H2 4.0/s
mass-energy-storage ×0.5/s : 2 临界光子 → 2 反物质 + 2 H2  ⇒ 产 H2 1.0/s
正确净需求 = 3.0/s
```

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "lib")
from dsp import Dataset
import boundary as BND
from stats import build_item_graph, cycles_of, is_roundtrip
ds = Dataset(); mat = None; rt = set()
for c in cycles_of(build_item_graph(ds)):
    r, p = is_roundtrip(ds, c)
    rt |= set(p) if r else set()
    mat = mat or (None if r else c)
b = BND.analyze(ds, mat, {"energy-matrix": 2.0, "antimatter": 1.0}, rt)
print(b.demand["hydrogen"])     # 修复前：4.0；修复后：3.0
print(b.surplus)                # 修复后：{}（此处无富余）
PY
```

修复后的完整验证见 `../case_h_auto_boundary.log` 的 H7–H12。
