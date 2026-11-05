#!/usr/bin/env bash
# Dyson Canvas P0 证据复现脚本
# 用法: ./run_all.sh
# 输出: outputs/*.log，并汇总 PASS/FAIL
#
# 依赖: Python 3 标准库 + （可选）GLPK 5.0 的 glpsol。
#       glpsol 不存在时案例 F 会自动降级为「仅自实现 + 独立可行性校验」，
#       并在输出中标注，不会把「未对照」写成「已对照」。
set -u
cd "$(dirname "$0")"
mkdir -p outputs

fail=0

run_case () {
  local f=$1 name=${1%.py}
  printf '%-30s ' "$name"
  if python3 "$f" > "outputs/$name.log" 2>&1; then
    echo "PASS  -> outputs/$name.log"
  else
    echo "FAIL  -> outputs/$name.log"
    fail=1
  fi
}

echo "── 算法案例 ──────────────────────────────────────────"
run_case case_a_basic_chain.py
run_case case_b_casimir.py
run_case case_c_multi_target.py
run_case case_d_hydrogen_loop.py
run_case case_e_edge_cases.py
run_case case_f_lp_validation.py
run_case case_g_boundary_policy.py

echo
echo "── 数据统计 ──────────────────────────────────────────"
printf '%-30s ' "stats"
if python3 stats.py > outputs/dataset-stats.log 2>&1; then echo "PASS  -> outputs/dataset-stats.log"; else echo "FAIL"; fail=1; fi
printf '%-30s ' "stats --regimes"
if python3 stats.py --regimes > outputs/dataset-stats-regimes.log 2>&1; then echo "PASS  -> outputs/dataset-stats-regimes.log"; else echo "FAIL"; fail=1; fi
printf '%-30s ' "stats --json"
if python3 stats.py --json > /dev/null 2>&1; then echo "PASS  -> outputs/dataset-stats.json"; else echo "FAIL"; fail=1; fi

echo
echo "输出行数统计:"
wc -l outputs/*.log | sed 's/^/  /'
exit $fail
