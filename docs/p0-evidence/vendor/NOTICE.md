# 第三方数据来源与授权说明

本目录（`vendor/`）保存的是**上游原始数据集的副本**，用于让证据目录在离线、
无上游克隆的环境下仍可复现。它不包含 Dyson Canvas 的任何业务代码。

## 1. 数据来源

| 项 | 值 |
|---|---|
| 上游仓库 | https://github.com/factoriolab/factoriolab |
| 文件 | `public/data/dsp/data.json`、`public/data/dsp/defaults.json` |
| 获取方式 | `git clone --depth 1 --filter=blob:none --sparse`，仅检出 `public/data/dsp` |
| 游戏版本 | DSP `0.10.29.21950`（见 `data.json` 的 `version` 字段） |
| 数据集规模 | 496 条配方 / 489 个物品 |
| SHA256 | `940d5d6eac72b250ddb87cd717189e87eb9a1645d28cfb6081b28a754dc1684b` |
| 文件大小 | 268224 bytes |

复核命令：

```bash
shasum -a 256 vendor/dsp-data.json
python3 -c "import json;d=json.load(open('vendor/dsp-data.json'));print(d['version'])"
```

## 2. 授权

- **代码与数据集**：MIT License，© 2020-2026 Doug Broad。全文见 `LICENSE-factoriolab.txt`。
  MIT 允许复制、修改、再分发，条件是保留版权声明与许可声明 —— 本文件即该声明。
- **游戏名称、图标、文本等素材**：版权归 Youthcat Studio 所有，**不在 MIT 授权范围内**。
  本次只复制了 `data.json`（数值与英文名称）与 `defaults.json`，
  **未复制 `icons.webp`、`i18n/*.json` 等素材文件**。
  若将来需要图标或本地化文本，必须另行确认授权，不能因数据集是 MIT 就推定素材也可分发。

## 3. 数值未被修改

`vendor/dsp-data.json` 是上游文件的**逐字节副本**，未做任何编辑。
所有裁剪与归一化都发生在 `extract_subset.py` 生成的 `dsp-subset.json` 中，
且只做字段裁剪与结构转换，不改数值。
