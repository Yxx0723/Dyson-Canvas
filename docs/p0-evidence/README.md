# P0 算法验证证据

本目录是 `docs/开源项目调研与技术借鉴报告.md` 的**可复现证据**，不是 Dyson Canvas 的业务代码。

## 这是什么

一套零依赖（仅 Python 3 标准库）的独立实现，用于验证六个参考项目的关键算法结论。
与参考项目源码**无复制关系**——公式来源在报告第 2、4 章逐条标注路径与函数名。

## 怎么跑

```bash
./run_all.sh
```

约 3 秒完成（本机实测 2.7 s，含 10 条故障注入自检），输出写入 `outputs/*.log`，退出码 0 表示全部通过。

**退出码是唯一的通过标准。** 每个案例的判定由 `lib/report.py` 的 `Checker` 汇总，
任一断言失败即返回非零，`run_all.sh` 随之报 FAIL。
`--no-fault` 可跳过故障注入自检（较慢）。

### 当前实测结果（v4）

| 环境 | 判定 | 日志行数 | 退出码 |
|---|---|---:|---:|
| 有 GLPK | **81 PASS / 0 FAIL / 0 SKIP** | 1203 | 0 |
| 无 GLPK | **80 PASS / 0 FAIL / 1 SKIP** | 1203 | 0 |

9 个案例 + 3 份统计 + 1 项 JSON 合法性检查 + 10 条故障注入自检。
逐案例：A 6 / B 8 / C 6 / D 6 / E 8 / **F 21（无 GLPK 时 20+1 SKIP）** / G 7 / **H 13** / I 6。

> 本轮（v4）比 v3 多 14 项：案例 F 增加 8 条证书/枚举反例回归（F12–F19，67 → 75），
> 案例 H 从 7 项增至 13 项（75 → 81）：副产抵扣、双目标全局账、旧口径反证、富余处置。

### 开箱即用，不依赖任何外部路径

`vendor/dsp-data.json` 是 FactorioLab 上游数据集的原始副本（496 条配方 / 489 个物品，
DSP `0.10.29.21950`，SHA256 `940d5d6e…`），随本目录一起分发。因此：

- **不需要**克隆 FactorioLab，**不需要**联网；
- `extract_subset.py` 与 `stats.py` 的查找顺序是「命令行参数 → `vendor/` → `/tmp/p0-refs/`（开发期路径）」，找不到时给出明确报错而不是静默失败；
- 在 `vendor/` 存在的情况下，即使删掉 `/tmp/p0-refs` 与 `/tmp/glpk`，**9 个案例与 3 份统计仍全部通过**（已实测，输出 1203 行；案例 F 的第三方对照如实降级为 1 条 SKIP）。

这一点很重要：`R0` 口径（含 307 条 `technology` 研究配方）必须读**原始** `data.json` 才能算，
而 `dsp-subset.json` 在抽取阶段就已把研究配方剔掉。若只带子集，`--regimes` 的 R0 会静默退化成 R1 的数字。

### 可选依赖：GLPK

`case_f` 需要 `glpsol` 才能做第三方对照。**未安装时不会失败**，而是：

- 把 8 个模型的 `GLPK 一致` 逐条标注为 `GLPK SKIP（未安装）`；
- 把汇总项 `F20 第三方对照（GLPK）` 标记为 `[SKIP]`（不计入通过，也不计入失败）；
- 首行写明 `**未安装 —— 第三方对照将标记为 SKIP**`。

不会把「未对照」写成「已对照」。**GLPK 不是通过的必要条件**：8 个模型的判定以 ORACLE 为准，
独立基准由顶点枚举 / RREF / 射线证书提供，三者都不依赖 GLPK。

重建方式（本机已验证）：

```bash
curl -sSL -o /tmp/glpk.tar.gz https://ftp.gnu.org/gnu/glpk/glpk-5.0.tar.gz
tar xzf /tmp/glpk.tar.gz -C /tmp && cd /tmp/glpk-5.0 \
  && ./configure --prefix=/tmp/glpk --disable-shared && make -j4 && make install
```

`lpio.find_glpsol()` 会依次查找 `/tmp/glpk/bin/glpsol` 与 `PATH` 中的 `glpsol`。

单独跑某个案例：

```bash
python3 case_d_hydrogen_loop.py
```

## 数据来源

`dsp-subset.json` 由 `extract_subset.py` 从
[`factoriolab/factoriolab`](https://github.com/factoriolab/factoriolab) 的
`public/data/dsp/data.json` 抽取，只做字段裁剪，不改数值。

- 游戏版本：DSP `0.10.29.21950`
- 源文件 SHA256：`940d5d6eac72b250ddb87cd717189e87eb9a1645d28cfb6081b28a754dc1684b`
- 许可：MIT，© Doug Broad and contributors
- **物品名称/图标原属 Youthcat Studio，不在 MIT 授权范围内**

重新生成（默认用 `vendor/dsp-data.json`，无需原仓库）：

```bash
python3 extract_subset.py                                  # 用 vendor/ 里的副本
python3 extract_subset.py /path/to/dsp/data.json           # 指定其他副本
```

## v4 修订的重点（第三轮复验）

三项都先独立复现、再改代码、再补回归；修复前的原始输出保存在 `outputs/v4_repro/`。

| # | 问题 | 复现日志 | 修复位置 | 回归 |
|---|---|---|---|---|
| 1 | 无界判据 `c_j<0 ∧ A[:,j]≤0` 在等式约束下不成立；只验方向、不验可行点 | `v4_repro/r1_ray_pre_fix.log` | `lib/linalg.py` `verify_ray_certificate` / `prove_unbounded` | F12–F15 |
| 2 | 顶点枚举按原始行数选列，重复约束 → `no_vertices` 被当成不可行 | `v4_repro/r2_enum_pre_fix.log` | `lib/linalg.py` `independent_rows` / `enumerate_optimum` | F16–F19 |
| 3 | 环外展开只累计消耗、不抵扣副产，全局多出 1/s 氢气 | `v4_repro/r3_byproduct_pre_fix.log` | `lib/boundary.py` `expand_external` / `global_balance` | H7–H12 |

对应的故障注入（`P0_FAULT=...`）：`ray_column_only`、`enumerate_raw_rows`、`boundary_no_byproduct`。
三条都能让对应用例以非零退出码失败 —— 这是「断言不是装饰品」的证据。

## 文件说明

| 文件 | 作用 |
|---|---|
| `lib/dsp.py` | 数据集索引、有效配方、递归求解器、物料守恒校验 |
| `lib/linalg.py` | 矩阵分类、两阶段单纯形（Fraction + Bland）、顶点枚举（按有效行秩）、射线证书 |
| `lib/report.py` | 统一的证据输出格式与 `Checker` |
| `lib/boundary.py` | SCC 边界推导、环外展开（速率 + 全部输入输出）、净需求、全局物料账 |
| `lib/policy.py` | 按 `fuel_types` / `consumption` 推导处置路径（仅 chemical 燃烧进 LP） |
| `lib/faults.py` | 故障注入开关（9 个注入点） |
| `lib/lpio.py` | LP 模型 I/O、GLPK 调用与状态消歧、独立可行性校验 |
| `case_a_basic_chain.py` | 电磁矩阵基础链，对照手算基准 |
| `case_b_casimir.py` | 卡西米尔晶体，多级/多输入/配方切换（B6 为预期失败） |
| `case_c_multi_target.py` | 双目标共享中间产物，线性可加性硬校验 |
| `case_d_hydrogen_loop.py` | SCC 证明氢气循环 + 方阵求解 + 松弛变量 LP |
| `case_e_edge_cases.py` | 无解 / 多解 / 负速率 / 原料不足 / 数据缺失 / 奇异 |
| `case_f_lp_validation.py` | 自研单纯形 vs ORACLE vs GLPK；证书与枚举反例回归 |
| `case_g_boundary_policy.py` | 燃料分派、焚烧开关、加权 vs 字典序 |
| `case_h_auto_boundary.py` | 边界自动化、副产抵扣、全局物料账（含旧口径反证） |
| `case_i_graph_stats.py` | 图节点统计回归（纯输出节点 / 无输入配方） |
| `verify_faults.py` | 退出码自检：改坏逻辑必须失败 |
| `stats.py` | 数据集统计（唯一口径来源）；`--regimes` 口径对照；`--json` 机器可读 |
| `extract_subset.py` | 从上游 `data.json` 抽取自包含子集（含 SHA256 provenance） |
| `dsp-subset.json` | 抽取结果，189 条生产配方 × 182 个物品，案例的唯一数据输入 |
| `vendor/dsp-data.json` | 上游原始数据集副本（496 条配方），供 R0 口径与再抽取使用 |
| `vendor/dsp-defaults.json` | 数据集的 `defaults`（默认建筑/增产剂等） |
| `run_all.sh` | 一键复现全部案例与统计 |

## 输出目录

| 目录 | 内容 |
|---|---|
| `outputs/*.log` | 最近一次运行（有 GLPK）的输出 |
| `outputs/_env_with_glpk/` | 有 GLPK 的存档快照，案例 F 21 项 |
| `outputs/_env_without_glpk/` | 无 GLPK 的存档快照，案例 F 20 项 + 1 SKIP |
| `outputs/_archive_v3/` | 本轮修订**前**的 v3 日志（含两种环境），用于对照 |
| `outputs/v4_repro/` | 三个反例的**修复前**重现输出 |

两个环境都必须 exit=0。GLPK 缺失不影响任何结论的生成，只把「第三方佐证」标记为未执行。

## 断言有效性

`python3 verify_faults.py` 会对 10 条关键守卫做「改坏 → 观察退出码」的实验：
正常跑必须 exit=0，注入故障必须 exit≠0，且日志里出现预期的失败标记。三者都满足才算这条守卫有效。

当前结果：**有效守卫 10/10**。其中 v4 新增 3 条，分别对应上面三项修复。

## 已知边界

- 引擎主算用 `float`，仅在校验与判定环节用 `Fraction`。报告第 7.3 节给了理由。
- 功耗只算工作功耗（`usage`），未建模待机功耗（`drain`）。
- 采矿设备单列统计，不计入制造设备合计。
- 案例 E 的「数据缺失」一项是**人为故障注入**，已在输出中明确标注。
- `case_f` 的 GLPK 对照受限于其 `-o` 文本解只有 6 位小数，目标值容差取 `1e-5`、解向量取 `1e-4`。
- **本目录只验证算法思路，不构成对 TypeScript 实现的验证**——TS 版本尚未编写。
- 故障注入覆盖 10 条守卫；案例 E 的 8 项与统计脚本的多数断言尚未逐一验证「改坏能否发现」。
- 边界自动推导只在一个物料循环上验证过；第二个循环、部分外购混合比例、>10 个边界物品均未测。
- **全局物料账的闭合范围有限**：覆盖环内平衡行 + 目标 + 环外配方的全部输入输出；
  环内配方的入边原料（coal / crude-oil）按「边界外无限供给」处理，不建行，不计入闭合。
- **顶点枚举有规模上限**（rank ≤ 5、n ≤ 24、组合数 ≤ 2e5）；超限返回 `too_large`，不是「不可行」。
- **处置通道只建模了 chemical 燃烧**：核燃料棒 / 反物质燃料棒 / 透镜消耗 / 满电池↔空电池
  都已识别但未进 LP（状态往返有回流，单列会算错账）。
