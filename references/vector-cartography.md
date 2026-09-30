# 矢量匹配与制图综合

复用 `daily.py` 的输入指纹、稳定 ID、投影计量、暂存/回读/新目录发布；长度参数为米，面积参数为平方米。需要已知 CRS、有效二维几何。新操作保持原数据不变；形态派生、对应关系和综合几何均为 `candidate`，结果包为 `hold`。候选计算通过不构成业务、历史或地理身份认可。

```bash
python3 gis-kit/scripts/daily.py match source.gpkg --right reference.gpkg --params matching.json --output /external/new-match
```

## 形态与加权汇总

- `morphology`：`id`、可选 `analysis_crs`、`method=metrics|convex_hull|concave_hull|oriented_envelope`。凹包 `ratio` 在 0—1 内，`allow_holes` 默认 false；复用 GEOS。每个输入对象独立计算，多点集先显式组成 MultiPoint。输出 `long_m/short_m/aspect/direction_deg/compactness` 均量测原始几何；长轴方向从投影东向逆时针、模 180°，正方形无唯一方向；点/线的面积紧凑度和退化长宽比为空。包络不等于观测边界。
- `weighted_summary`：`group/value/weight/quantity_type/weight_meaning`。`quantity_type=ratio` 按显式分母或其他声明权重求加权均值；`category` 保留每类权重、数量和占比，不自动选择多数类。权重非负有限；缺值拒绝，零权重组的比率/占比为未定义。普通 `summarize` 的总量语义不变。
- `oriented-crop.py IMAGE --params JSON --output NEW_DIR`：`center_xy`、整数 `size_px`、`angle_deg`、可选 `source_pixels_per_output_pixel`、`resampling=nearest|bilinear`。非生成式图像采样，输出 RGB PNG、原图 SHA、双向 3×3 映射；图像边缘坐标，像元中心为 x+0.5/y+0.5，y 向下，正角顺时针。越界拒绝；默认最多 2500 万输出像元。不写回原图，类别/证据样本应选 nearest，双线性结果须保留派生标记。

## 跨 ID 对应、分段与受控采用

`match` 与 `match_split` 共用参数：

```json
{
  "id": "source_id",
  "right_id": "reference_id",
  "analysis_crs": "EPSG:32645",
  "radius_m": 30,
  "max_angle_deg": 20,
  "min_overlap_m": 5,
  "attributes": {"name": "name"},
  "require_attributes": false,
  "max_pairs": 100000
}
```

匹配限 Point ↔ Point 或简单、开放 LineString ↔ LineString；多部件先显式拆分。输出双方 ID、最短距离、部分线 Hausdorff 距离、无向弦方向差、双方沿线区间、属性逐项相等证据。空间索引限候选范围；线区间由端点投影得到，强弯曲、回折和复杂分岔可能拒绝合法对应，本接口不保证召回。距离与方向阈值共同筛选；属性默认仅作证据，开启 `require_attributes` 后也参与过滤。匹配分数不伪装概率，保留所有候选、未匹配双方和区间重叠多解；两个不重叠的分段候选不会仅因数量为二被标多解。

`match_split` 仅按候选区间端点切来源线，输出 `segment_id/source_id/start_m/end_m/target_ids/match_status`；未匹配区间和多解区间均保留，回核重建长度与形状。目标属性不自动复制。

`match_transfer` 使用同一匹配参数，另要求 `approved_by/source_sha256/reference_sha256/selections/transfer_fields`。例如 `selections={"source-a":"reference-b"}`，`transfer_fields={"new_name":"name"}`。指纹过期、选择非候选或字段覆盖均拒绝。仅显式采用字段，原几何不变；部分线必须先切分、复核，再转移。未选择对象保留空新字段。含空值且超过 Float64 精确整数范围的整数字段拒绝，需上游显式转为字符串。

`edge_match`：`id/right_id/max_displacement_m/endpoint_pairs`，每个端点对含 `source_id/target_id/source_end/target_end`，端点索引只允许 0 或 -1。只移动指定端点及来源中精确共点的连接端点，不执行全线橡皮片。距离越限、冲突端点、塌缩、自交和新增来源线交叉拒绝；保留前后 WKT、面积和位移影响。输出候选随后走既有 `cleanup_adopt` 的指纹绑定、位移/面积/结构和业务规则门槛。接边位置及语义联系由审核者决定；历史差异不自动对齐现代底图。

## 尺度综合与来源保留

`generalize` 的通用参数为 `id/method/analysis_crs/protected_ids`：

1. `aggregate_points` / `aggregate_polygons` 使用 `distance_m` 建立单链接距离组，稳定 ID 决定顺序。受保护对象独立保留；点组用质心表达，面组取精确 union，保留间隙为多部件，不桥接未知空间或填孔。输出派生 ID、所有来源 ID、来源/输出面积和最大位移。多个距离链可形成跨度大于阈值的组，阈值限制直接邻接距离。
2. `regularize_buildings` 使用成熟的最小旋转矩形，要求 `max_displacement_m/max_area_change_m2`。保护孔洞、多部件、指定对象及共享边；超限或新增邻居冲突保留原形，候选之间新增交叠则整体拒绝。适用近矩形独立建筑；复杂楼形的正交化、楼群整体移位未实现。共享边简化继续用既有 `cleanup method=coverage_simplify`，不逐面替代。
3. `thin_network` 要求 `from/to/priority/keep_priority_at_least`；节点身份与端点坐标一致，交叉处是否连通由显式节点决定。NetworkX 最大生成森林保留所有原节点的无向连通分量，再保留重要/受保护边；排除低优先级冗余环边并逐条记录 ID/WKT/原因。不保留有向通行、绕行长度、转向或导航最优性；不得替代路由网络。相同优先级按排序后的稳定 ID 确定顺序。
4. `color` 对有效无交叠面建立接触图，`point_touch` 默认 true；贪心拓扑着色保留 `color_index` 和相邻 ID 对。编号只用于区分相邻面，不对应语义类别，也不承诺最少颜色。

## 目标尺度输出与实际标签检查

`cartographic-map.py DATA --params JSON --output NEW_DIR` 复用 `plot.py`，输出 GPKG、PNG、HTML 与记录。参数：`id`、可选 `label/color_field`，`scale`（比例尺分母）、`map_width_mm/map_height_mm/dpi`；`center_xy` 使用分析 CRS 单位。所有对象默认必须完整落入范围，明确 `allow_clipping=true` 时记录被裁对象并保持 hold。颜色字段只接受 0—19 的整数显示编号。

固定图轴物理尺寸，按最终渲染器图轴像素宽反算比例尺；PNG 使用嵌入 DPI 打印，禁止适合页面缩放。这里是投影线性比例尺，不承诺整个投影区地面比例尺恒定。标签审核读取**最终布局、实际字体和 DPI 下文字框**，输出重叠、越界与文字位置。默认 `label_collision_policy=report`，冲突保持 hold；显式 `omit` 可隐藏冲突标签，`max_labels` 限数量，被省略文字逐条记录，几何和来源对象始终保留。文字框通过仍需检查线面遮挡、读图负担和语义。现有 `atlas.py` 的对象 HTML/PNG 图册继续可用；本接口未添加 PDF 或 QGIS Atlas 后端。
