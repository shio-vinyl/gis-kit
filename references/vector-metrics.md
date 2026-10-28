# 矢量计量、指标与空间连接

## 单位与分析 CRS

`buffer.py` 的 `--distance`、`--field` 和 `join.py --max-distance` 均以米为单位。面积型字段一律以平方米输出；FAR、建筑密度、coverage/green ratio 是无量纲比值，不做面积单位换算。对于投影 CRS，脚本读取水平轴单位换算因子，要求两个水平轴单位一致且可识别；面积乘以 `meters_per_unit²`，距离乘以 `meters_per_unit`。源 CRS 缺失、计量几何为空/无效或投影轴单位未知时拒绝，工具不静默 `make_valid`。

默认分析 CRS 为来源中已有的投影 CRS。经纬度输入只有在全部参与图层的合并范围都落入单一 UTM 带、经度宽度不超过 6°、纬度宽度不超过 8°且纬度在 UTM 的 −80° 至 84° 范围内才自动选择局部 UTM。更大范围或跨带输入必须显式给出适用的投影 CRS；调用者负责该投影在完整研究区内的适用性。

计算中的 CRS 不替换输出 CRS：缓冲输出回到输入 CRS；指标输出沿用 parcel/boundary CRS；空间连接的 left/inner 输出沿用 left CRS，right 输出沿用 right CRS。程序在统计行或匹配报告中注明实际 analysis CRS 与方法。

## 缓冲与空间连接

```bash
python3 gis-kit/scripts/buffer.py roads.gpkg --distance 100 --output roads_100m.gpkg
python3 gis-kit/scripts/join.py --left buildings.gpkg --right stops.gpkg \
  --predicate nearest --max-distance 500 --agg first --output building_stops.gpkg
```

`join.py` 为来源要素生成 `left_source_id` / `right_source_id`；默认值基于文件读取顺序的 `left:<position>` / `right:<position>`，也可提供唯一、非空的 `--left-id-field` / `--right-id-field`。输入顺序不变时 ID 稳定。属性重名使用 `--suffix` 作确定性重命名；若生成名仍冲突则拒绝。`--fields` 选择需要带入的对侧字段，省略时选择所有属性字段。

- `--agg first` 按对侧源行顺序取完整首行，包括首行的 null；未匹配输出保留字段并写 null。
- `--agg all` 每个匹配关系输出一行；left/right 保留未匹配的主侧行，inner 仅保留匹配。`right` 反向以右侧为主侧；nearest 下每个右要素查找最近左要素。
- `--agg count` 对选择字段分别统计非空值数；`match_count` 单独记录空间匹配关系总数。`sum` 跳过数值 null、全 null 保持 null；`mean` 同理。聚合行同时保存 `matched_source_ids` JSON 列表。
- Nearest 的 `--max-distance` 和 `distance_m` 均为米。未设上限时查询最近关系而不裁切距离。报告打印左右两侧的匹配、未匹配数、匹配关系数、策略与 analysis CRS，也可用 `--report-json` 保存。

## 按编号挂接指标表

把说明书或 Excel 里的指标（建筑量、容积率、配套）按地块编号挂到地块上。这一步是属性挂接，不看几何，也不需要 CRS。

```bash
python3 gis-kit/scripts/join.py --left parcels.gpkg --right 指标表.xlsx --right-sheet 指标表 \
  --on parcel_id=地块编号 --normalize id --fields 建筑量,容积率 --agg first \
  --output parcels_indicators.gpkg --report-json join_report.json
```

- **`--on 左字段=右字段`**：两边字段同名时写一个名字即可。只支持 `--how left` 或 `inner`，结果保留左侧的几何和 CRS。`--predicate`、`--max-distance`、`--analysis-crs` 在这里不适用，给了就报错。
- **右侧输入**：可以是 CSV、Excel 或任意矢量文件。
  - 表格的单元格一律先按文本读入，编号的前导零不会丢。其他列只有当整列都是数字时才转成数值，因此可以用 `sum` / `mean`。
  - Excel 用 `--right-sheet`（表名或从 0 开始的序号）选工作表。
  - 表头不在第一行时（说明书表格常有标题行），用 `--right-header-row`（从 1 开始）指定。
  - CSV 是 GBK 等编码时用 `--right-encoding`。
  - 表头里有重名列时报错。
- **`--normalize id`**：按地块编号的写法规范化后再比对：
  - 全角转半角（NFKC）；
  - 各种连字符（`－`、`—`、`–` 等）统一为 `-`；
  - 去掉空格，字母转大写；
  - 每段数字去掉前导零（`01-03`、`1-3`、`１－３`、`01—03` 都视为 `1-3`）；
  - Excel 读成 `103.0` 的编号按 `103` 处理。

  默认 `none`，按原样比对。
- **聚合**：沿用前面的 `--agg` 规则。右侧有重复编号时，`first` 取第一行，`sum` 加总，`all` 每行输出一条；`match_count` 记录匹配到的行数。
- **报告（`--report-json`）**：除了匹配数，还有：
  - `key`：两侧字段和规范化方式；
  - `left_unmatched_keys` / `right_unmatched_keys`：两侧对不上的编号（原样）；
  - `left_without_key` / `right_without_key`：编号为空的行数；
  - `*_duplicate_keys`：同一侧的重复编号，以及对应的原始写法；
  - `*_collisions`：写法不同、规范化后却变成同一个编号的情况；
  - `*_normalised`：每一处 `[原写法, 规范化后]` 的对照。
- **地块与规划控制单元**：两者的编号可能撞号。挂接前先按 `control_type` 筛出需要的一类。

GeoPackage/GeoJSON/FlatGeobuf 文件由安全写入器整文件暂存、重读校验后发布；默认不覆盖任何现有输出，也不允许输出覆盖输入。确需替换时显式使用 `--overwrite`。CSV 使用同目录临时文件原子发布。多文件旧格式保持既有写法，不保证原子性。

## 建筑与用地指标

```bash
python3 gis-kit/scripts/indicators.py summary --parcels parcels.gpkg --parcel-id parcel_id \
  --buildings buildings.gpkg --floors-field floors --assignment area \
  --analysis-crs EPSG:32618 --output indicators.gpkg --report-json assignment.json
```

`far`、`density`、`summary` 都支持 `--assignment area|unique`，默认 `area`。`area` 将建筑 footprint 与各地块的交叠面积作为分配面积；楼层数视为整栋均匀，FAR 楼面面积按“交叠面积 × 楼层数”计算。`unique` 把整栋 footprint/楼面面积分配给交叠面积最大的地块；等面积时按地块源行顺序选第一个。只有正面积交叠才算匹配。正面积地块重叠会拒绝，避免同一建筑面积被重复计入；建筑全部在地块外则记为未匹配。建筑几何与楼层数须有效；楼层必须为正的有限整数。

面积字段名显式带 `_m2`：`parcel_area_m2`、`building_area_m2`、`total_floor_area_m2`、`green_area_m2`。FAR、`density` 与 `green_ratio` 无量纲。assignment 报告含匹配/未匹配建筑数、来源/分配/未分配 footprint 面积、部分外溢数、分配策略与 analysis CRS；可输出 JSON。

## 空间统计与分区汇总

`stats.py spatial` 接受 `--study-area FILE` 和可选 `--analysis-crs`。若未提供研究区，为兼容旧调用仍计算来源范围 bbox，并在报告中明确标为 bbox fallback 假设；`--bbox-fallback` 可显式确认该假设。正式密度/最近邻分析应优先提供实际研究区。研究区模式按要素质心是否被边界覆盖来选样本，分母为研究区并集面积。距离以米、面积以平方米呈现；bbox 零面积时仍输出最近邻距离，密度、期望距离与 NNI 标记为 N/A。NNI/CSR参考距离仅为描述量，不生成随机/聚集/离散分类，也不声称统计显著性。

`stats.py cross` 要求 zone ID 唯一且非空。默认 `--predicate within` 排除只落在区边界上的目标；`--predicate intersects` 包含边界接触，目标可同时计入多个相交分区。报告包含分析 CRS、总匹配对数及至少匹配一次/未匹配目标数。全未匹配分区 `count=0`；汇总字段全为 null 时 `sum_*` 保持 null。

## 例子

```bash
python3 gis-kit/scripts/stats.py spatial points.gpkg --study-area study_area.gpkg
python3 gis-kit/scripts/stats.py cross --input zones.gpkg --target points.gpkg \
  --zone-field zone_id --sum-field amount --predicate intersects --analysis-crs EPSG:32618
```
