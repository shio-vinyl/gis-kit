# 通用路网、合法接入与需求覆盖

`network.py` 的 `topology="source_vertices"` 使用源节点身份建图，支持中段接入、显式接驳成本和三态需求覆盖。旧端点模式保持兼容；旧模式仍不计接驳成本，不得混用两种结果口径。

```bash
"$PY" "$GIS_SKILL/scripts/network.py" roads.gpkg \
  --origins demand.gpkg --facilities facilities.gpkg \
  --access-areas access-areas.gpkg --barriers barriers.gpkg \
  --params params.json --output "$RUN/network"
```

## 输入契约

道路为带 CRS 的有效二维 LineString。`vertex_nodes` 映射的字段存 **JSON 字符串数组，每个坐标对应一个源节点 ID**；一条道路可以含多个顶点。程序逐相邻顶点拆段，源 ID 相同才连通；同节点坐标误差最多 1e-6 米，几何交叉或同坐标不同 ID 均不合并。桥隧交叉保持分离；坡道两端可共享真实源节点，在不同 level 间连接。缺源节点的线网须先审核拓扑，程序不凭平面交叉猜测通行关系。

道路 `level` 与点 `point_level` 必须明确，点仅接入匹配层级。道路 `access` 必须规范化为 `allowed/blocked/unknown`；`direction` 为 `both/forward/reverse`，方向相对于坐标顺序。调用方按显式出行模式解析源 oneway、门禁等规则，保留原标签与依据；程序不默认把机动车 oneway 套到步行。最快路径另需正数 `speed_kmh`。

两个接入图层必须显式提供，可为空，固定字段为 `level`、`access`：

- `access-areas`：Polygon/MultiPolygon，表示已审查的接驳许可范围。同层 allowed 面取并集；整个接驳线受覆盖才确认。与 unknown 面相交保留未知，与 blocked 面相交拒绝。无许可面、超出面或穿越未声明可通行区域均为未知。
- `barriers`：Point、线或面，约束道路和接驳；同层 blocked 障碍相交拒绝，unknown 障碍使通行未知，allowed 不覆盖其他禁令。墙、水域可作为 blocked 几何，未核验门禁可作为 unknown。**首版障碍采用保守整段策略：触及源相邻顶点段即限制整段**，不推导门的开口宽度。上游应在入口、门或障碍位置提供足够细的源节点；整段限制可能少算可达范围。

“合法”始终相对于输入的许可与障碍证据；缺失围墙、未识别水域、错误 level 或错误源连接均可能影响现实正确性。没有现场核验时保留 `candidate`。

## 参数

```json
{
  "topology": "source_vertices",
  "edge_id": "id", "vertex_nodes": "nodes",
  "level": "level", "access": "access", "direction": "direction",
  "speed_kmh": "speed", "mode": "shortest", "travel_mode": "walking",
  "analysis_crs": "EPSG:32630",
  "origin_id": "id", "facility_id": "id", "point_level": "level",
  "demand_weight": "weight",
  "snap_m": 20, "budget": 500, "connector_speed_kmh": 4.8,
  "turn_restrictions": "not_modelled",
  "cost_assumptions": "Declared scenario; speed is not an observation",
  "access_assumptions": "Reviewed permission envelopes and normalized source rules",
  "facility_sets": {"baseline": ["f1"], "expanded": ["f1", "f2"]},
  "max_pairs": 10000, "max_comparisons": 2000000
}
```

所有阈值及权重须有限、非负，速度须正。`mode=shortest` 成本为米；`fastest` 为秒，路段速度与接驳速度分别生效。`travel_mode` 是必填记录字段，实际限制由输入字段承载。投影轴为英尺时明确换算为米。

接入在同层非 blocked 源段中，按距离、源段 ID 排序，选择第一个未被显式禁止的最近投影接驳；选定位置切边、保留方向，**不允许沿一条路的两个接入点绕过单行约束**。未知接驳可以被选中，但不会产生确认覆盖。每点使用单一固定接入，不优化所有入口，也不自动延长接驳寻找更远已知入口；多入口对象由调用方显式准备需求/设施入口模型。接驳成本含起点和终点两段，不允许超出 `snap_m`，恰在边界允许；路线方向为需求 → 设施。

## 输出与三态口径

输出包包含 `graph.gpkg`（含 blocked/unknown 的有向分段证据与成本）、`connectors.gpkg`（非零接驳）、`routes.gpkg`（已知路线的网络部分）、`service.gpkg`（扣除起点接驳成本后的出发可达路段）和 `record.json`。零长度接驳/同节点路线没有线几何，成本与节点仍在记录中。路线几何不含接驳线，总成本已包含两端接驳；用 OD 的 `edge_keys` 累加图成本，再加 `connector_cost` 可回算。

求解确认图（仅 allowed）与可能图（allowed + unknown）：

1. `covered`：确认路径加两端确认接驳的成本不超过阈值。
2. `uncovered`：已确定接入且在可能图中也无法在阈值内到达；仅对所给图、固定接入与规则成立。
3. `unknown`：接入失败，或阈值内只能依赖未知通行。较大的阈值可以让原先 uncovered 变成 unknown，这是可能图中新路径落入阈值的结果。

`coverage` 每设施集按需求 ID 聚合一次，不按设施或 OD 行重复计权重。保留逐需求状态、三态数量/权重、总分母；`coverage_rate=covered/total`，`possible_coverage_rate=(covered+unknown)/total`。全零权重时比率为 null；空设施集确认无覆盖；设施接入失败在非空设施集中传播未知。默认设施集是全部设施，未声明 `demand_weight` 时仅交网络与起点数量，**不输出人口/需求权重覆盖率**。需求权重的单位、来源与代表性由调用方声明，不能用服务面面积替代人口。

图层几何、属性、CRS 写出后回读，输入哈希前后核对，事务式输出目录不覆盖既有结果。服务结果只含有向路段；没有等时圈面、高级转向、GTFS、2SFCA 或设施优化。比较 guard 超限直接失败，不截断需求或 OD。

## 验证入口

- `tests/test_network_access.py`：手算中段/接驳、桥隧源身份、单行、墙/水域许可、门禁、未知捷径、断网、英尺换算、三态去重、写出回读和参数拒绝。
- `tests/run_network_access_acceptance.py --osm-xml /absolute/source.osm --output /absolute/new-run`：独立、离线的真实源文件链；使用 OSM 原始节点和标签，归一化策略仅作为验收情景，设施/需求权重为构造值。逐 OD 成本及三态权重回算，生成检查图；它不承担生产 OSM 导入职责，也不证明现实人口服务水平。
