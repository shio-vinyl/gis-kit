# 矢量面拓扑检查

`gis-kit/scripts/topo.py` 用于多边形层的面积、重复几何、无效输入、覆盖缺口和面间重叠诊断。它输出诊断结果，不会修复几何，也不证明边界在历史、法律或业务语义上正确。

## 命令

```bash
python3 gis-kit/scripts/topo.py parcels.gpkg --layer parcels \
  --expected-coverage study-area.gpkg --expected-coverage-layer study_area \
  --exclusions permitted-holes.gpkg --exclusions-layer exclusions \
  --output topology.txt --output-errors topology-errors.gpkg --fail-on-error
```

结构化 JSON：

```bash
python3 gis-kit/scripts/topo.py parcels.gpkg --check gaps,overlaps,duplicates \
  --format json --output topology.json
```

面积阈值单位固定为平方米：`--sliver-threshold` 默认 `1`，`--overlap-threshold` 默认 `0.01`。分析只在双水平轴单位一致且可换算的投影 CRS 中进行；输入若为投影 CRS，沿用它并按其 CRS 单位换算面积。地理 CRS 仅在相关层**联合完整范围**满足受限本地 UTM 条件时自动选择 UTM（同一 UTM 带、经度跨度不超过 6°、纬度跨度不超过 8°，并处于 UTM 可靠纬度范围）。该范围包含有效源面，以及提供时的 expected coverage 和 exclusions。任一辅助层缺失 CRS 或投影轴单位未知、范围过宽或跨带时，需给出投影 CRS：

```bash
python3 gis-kit/scripts/topo.py parcels.gpkg --analysis-crs EPSG:26918
```

显式 CRS 的地理适用范围由调用者负责。缺失 CRS、无法验证线性单位或不可用的分析范围会明确报错。计算使用分析 CRS，报告记录其 CRS 和每单位换算因子；错误 GPKG 几何会回投到输入 CRS，输入数据本身不改写。

## ID、无效几何与诊断语义

默认来源 ID 按读入行位置生成，例如 `source:000000000003`；也可用 `--id-field` 指定唯一、非空字段。位置指输入读入顺序，不取决于 pandas 索引标签。默认来源 ID 字段名 `source_id` 与输入字段冲突时会拒绝执行，避免覆盖；自定义 ID 的值会保留为字符串。

缺失、空、无效及非面几何会在源 CRS 下逐条说明原因，从面积/拓扑运算中排除。脚本不调用 `make_valid`。全部几何均不可分析时仍返回诊断报告，并将状态标为 `incomplete`。`self-intersections` 是 `invalid-geometries` 的兼容别名；具体无效原因取自几何有效性诊断。

精确相等的面由 `duplicates` 单独标出。若同时请求 `duplicates,overlaps`，相等面只计入重复项，避免双重计数；只请求 `overlaps` 时，重复面也作为重叠报告。空间候选按读入位置对排序去重，再以数组运算分块求交与面积。

## 缺口判定

若给出 `--expected-coverage`，缺口定义为：

`expected coverage − 有效源面并集 − exclusions`

`--exclusions` 是显式获准的多边形洞；期望覆盖或排除层必须有 CRS、只包含非空有效面。若未提供 expected coverage，脚本只报告源面并集中可识别的内部孔洞，严重级别为 `info`，`basis=identifiable_hole_only`；它不会把这些孔洞断言为遗漏或错误。`--fail-on-error` 不把这种无覆盖依据的孔洞当作 error。

## 报告、GPKG 与资源边界

默认输出可读文本；`--format json` 输出结构化 JSON，其中 `summary.issue_count` 统计所有严重级别，`summary.error_count` 只统计 error，`issues_by_check` 按检查分类。每条 issue 含 `source_a_id` / `source_b_id`、输入行位置、面积、依据及几何类型。`--fail-on-error` 在存在 error，或有输入几何被排除、分析不可用、候选上限或输出上限导致结果不完整时返回非零；默认模式只在执行错误时返回非零。

`--output-errors` 写入按检查分层的 GPKG。先在目标同目录生成 staging 文件，关闭后重新读取，校验图层、行数、输入 CRS 和来源 ID。默认通过同目录原子 no-clobber 链接发布；即使目标在预检查后由其他进程创建，也会失败且不覆盖。目标已存在时必须显式给出 `--overwrite-errors`，才以原子替换发布。输出路径不得指向输入层、expected coverage 或 exclusions。报告输出也不得覆盖输入。

资源默认上限：每类最多保留 `--max-issues` 10,000 条 issue；错误总数仍按发现数统计，JSON 报告各类保留/遗漏数及 `incomplete` 状态。`--max-candidates` 默认最多检查 1,000,000 对候选；达到即停止后续成对检查并标记不完整。查询批次默认 64，并根据面数量收缩以限制候选数组；交集数组再按 10,000 对分块。GPKG 只写入保留的 issue 几何。这里对 issue 记录数有硬上限，对单条几何的顶点数及文件字节数没有硬上限；高顶点复杂几何仍可使输出文件偏大。
