#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 FactorioLab 的 DSP 数据集抽取自包含子集，供 P0 算法验证使用。

来源  : https://github.com/factoriolab/factoriolab (MIT, © Doug Broad and contributors)
原始   : public/data/dsp/data.json
说明   : 本脚本只做「字段裁剪 + 归一化」，不修改任何数值。
        生成的 dsp-subset.json 保留 provenance（源文件 SHA256、游戏版本），
        使验证结论可在不联网、不依赖原仓库的前提下复现。

用法:
    python3 extract_subset.py /path/to/factoriolab/public/data/dsp/data.json
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dsp-subset.json")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


HERE = os.path.dirname(os.path.abspath(__file__))
# 数据源查找顺序:
#   1) 命令行参数
#   2) 本目录 vendor/dsp-data.json（随交付物打包，离线可复现）
#   3) /tmp/p0-refs 下的上游克隆（开发期路径，重启后可能不存在）
SRC_CANDIDATES = [
    os.path.join(HERE, "vendor", "dsp-data.json"),
    "/tmp/p0-refs/factoriolab/public/data/dsp/data.json",
]


def resolve_src() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    for c in SRC_CANDIDATES:
        if os.path.exists(c):
            return c
    return SRC_CANDIDATES[0]


def main() -> int:
    src = resolve_src()
    if not os.path.exists(src):
        print(f"[FATAL] 找不到数据源: {src}", file=sys.stderr)
        print(f"        已尝试: {SRC_CANDIDATES}", file=sys.stderr)
        return 2

    raw = json.load(open(src, encoding="utf-8"))
    items = {it["id"]: it for it in raw["items"]}

    # ---- 归一化配方 ----
    recipes = {}
    for r in raw["recipes"]:
        # 科技（研究）配方不进入生产计算，与 DSP Helper 的处理一致
        if "technology" in r.get("flags", []):
            continue
        recipes[r["id"]] = {
            "id": r["id"],
            "name": r.get("name", r["id"]),
            "time": r["time"],
            "in": dict(r.get("in", {})),
            "out": dict(r.get("out", {})),
            "producers": list(r.get("producers", [])),
            "flags": list(r.get("flags", [])),
            "category": r.get("category", ""),
        }

    # ---- 机器（携带 machine 区块的物品）----
    machines = {}
    for iid, it in items.items():
        m = it.get("machine")
        if not m:
            continue
        machines[iid] = {
            "id": iid,
            "name": it.get("name", iid),
            "speed": m.get("speed", 1),
            "usage_kw": m.get("usage", 0),
            "drain_kw": m.get("drain", 0),
            "modules": m.get("modules", 0),
            "type": m.get("type", "electric"),
            "total_recipe": bool(m.get("totalRecipe", False)),
        }

    # ---- 增产剂（携带 module 区块、且属于 proliferator 的物品）----
    modules = {}
    for iid, it in items.items():
        mo = it.get("module")
        if not mo or "proliferator" not in mo:
            continue
        modules[iid] = {
            "id": iid,
            "name": it.get("name", iid),
            "proliferator": mo["proliferator"],
            "productivity": mo.get("productivity", 0),
            "speed": mo.get("speed", 0),
            "consumption": mo.get("consumption", 0),
            "sprays": mo.get("sprays", 60),
            "limitation": mo.get("limitation"),
        }

    # ---- 仅保留被引用到的物品 ----
    referenced = set()
    for r in recipes.values():
        referenced.update(r["in"].keys())
        referenced.update(r["out"].keys())
    referenced.update(machines.keys())
    referenced.update(modules.keys())

    slim_items = {}
    for iid in sorted(referenced):
        it = items.get(iid)
        if it is None:
            continue
        slim_items[iid] = {
            "id": iid,
            "name": it.get("name", iid),
            "category": it.get("category", ""),
            "stack": it.get("stack", 0),
            # fuel: 决定该物品能否作为燃料被焚烧 —— 是「放空策略」的数据依据，
            # 详见报告「LP 松弛变量的可执行性约束」一节
            "fuel": it.get("fuel"),
            "types": it.get("types", []),
        }

    out = {
        "_license": (
            "Dataset derived from FactorioLab (https://github.com/factoriolab/factoriolab), "
            "MIT License, Copyright (c) Doug Broad and contributors. "
            "Item names/icons originate from Dyson Sphere Program (Youthcat Studio) and are "
            "NOT covered by the MIT grant."
        ),
        "_provenance": {
            "source_repo": "factoriolab/factoriolab",
            "source_path": "public/data/dsp/data.json",
            "source_sha256": sha256_file(src),
            "source_bytes": os.path.getsize(src),
            "game_version": raw["version"],
        },
        "items": slim_items,
        "recipes": recipes,
        "machines": machines,
        "modules": modules,
        "limitations": raw.get("limitations", {}),
        "flags": raw.get("flags", []),
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)

    print(f"[OK] 写出 {OUT}")
    print(f"     items={len(slim_items)} recipes={len(recipes)} machines={len(machines)} modules={len(modules)}")
    print(f"     game_version={raw['version']}")
    print(f"     source_sha256={out['_provenance']['source_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
