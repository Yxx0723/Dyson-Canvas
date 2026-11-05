#!/usr/bin/env bash
# Dyson Canvas P0 证据复现脚本（v3）
# 用法: ./run_all.sh            全部运行，任一失败则整体返回非零
#       （9 个案例 + 3 份统计 + 退出码自检，约 3 秒）
#       ./run_all.sh --no-fault  跳过故障注入自检（较慢）
#
# 依赖: Python 3 标准库 + （可选）GLPK 5.0 的 glpsol。
#       glpsol 不存在时案例 F 的第三方对照会标记为 SKIP，不会算作通过。
set -u
cd "$(dirname "$0")"
mkdir -p outputs

fail=0
SKIP_FAULT=0
for a in "$@"; do
  [ "$a" = "--no-fault" ] && SKIP_FAULT=1
done

run_case () {
  local f=$1 name=${1%.py} tag=${2:-}
  printf '%-30s ' "$name$tag"
  if python3 "$f" > "outputs/$name.log" 2>&1; then
    local n
    n=$(grep -c '\[PASS\]' "outputs/$name.log" 2>/dev/null || echo 0)
    echo "PASS  ($n 项)  -> outputs/$name.log"
  else
    local nf
    nf=$(grep -c '\[FAIL\]' "outputs/$name.log" 2>/dev/null || echo '?')
    echo "FAIL  ($nf 项失败)  -> outputs/$name.log"
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
run_case case_h_auto_boundary.py
run_case case_i_graph_stats.py

echo
echo "── 数据统计 ──────────────────────────────────────────"
printf '%-30s ' "stats"
if python3 stats.py > outputs/dataset-stats.log 2>&1; then echo "PASS"; else echo "FAIL"; fail=1; fi
printf '%-30s ' "stats --regimes"
if python3 stats.py --regimes > outputs/dataset-stats-regimes.log 2>&1; then echo "PASS"; else echo "FAIL"; fail=1; fi
printf '%-30s ' "stats --json"
# 注意: stats.py --json 自己会写 outputs/dataset-stats.json，
# 若把 stdout 也重定向到同一路径，会在文件写入后再追加一行 "[OK] ..."，把 JSON 变成非法格式。
# 这里把 stdout 收到单独的 .out 文件。
if python3 stats.py --json > outputs/dataset-stats-json.out 2>&1; then echo "PASS"; else echo "FAIL"; fail=1; fi
printf '%-30s ' "stats json 合法性"
if python3 -c "import json,sys; json.load(open('outputs/dataset-stats.json'))" 2>/dev/null; then
  echo "PASS"
else
  echo "FAIL（outputs/dataset-stats.json 不是合法 JSON）"
  fail=1
fi

echo
echo "── 退出码自检（故障注入）────────────────────────────────"
if [ "$SKIP_FAULT" = "1" ]; then
  echo "  已按 --no-fault 跳过"
else
  if python3 verify_faults.py > outputs/verify_faults.log 2>&1; then
    printf '%-30s PASS  -> outputs/verify_faults.log\n' "verify_faults"
  else
    printf '%-30s FAIL  -> outputs/verify_faults.log\n' "verify_faults"
    fail=1
  fi
fi

echo
echo "输出行数统计:"
wc -l outputs/*.log | sed 's/^/  /'
echo
if [ "$fail" = "0" ]; then
  echo "总体: PASS（全部案例、统计与退出码自检通过）"
else
  echo "总体: FAIL（见上方与 outputs/*.log）"
fi
exit $fail
