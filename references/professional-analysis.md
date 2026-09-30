# 专业分析与候选比较

这些接口显式调用，不自动进入标注或日常配方。均读源文件、写新的仓库外目录；`record.json` 的 `candidate` / `hold` 不代表业务采用。记录输入与实现指纹、参数、资源；不安装后端。完整参数示例和真实文件链在 `tests/run_professional_acceptance.py`，合成永久回归在 `tests/test_professional.py`。

## 路网与设施：`network.py`

保真源节点建图、受约束中段接入与需求权重覆盖使用 [路网可达与需求覆盖接口](network-access.md)。以下为兼容保留的旧端点模式，不自动升级成本口径。

```bash
$PY gis-kit/scripts/network.py edges.gpkg --origins origins.gpkg \
  --facilities facilities.gpkg --params network.json --output "$RUN/network"
```

参数为 `edge_id/from/to/direction/speed_kmh` 字段映射、`origin_id/facility_id`、`analysis_crs`、`mode=shortest|fastest`、`snap_m`、`budget`、`turn_restrictions="not_modelled"`、非空 `cost_assumptions`。可选 `facility_sets` 是方案名到设施 ID 数组；存在 `baseline` 时输出新增/失去服务的起点 ID。`max_pairs` 默认 10000。

输入必须是有效二维 LineString 与明确拓扑节点 ID；同节点端点在米制换算后误差不得超过 1e-6m。同坐标不同 ID 不合并，几何交叉不自动连通。方向值 `both/forward/reverse`，最快路径显式正速度 km/h；最短路径成本为米，最快路径为秒。复用 NetworkX 有向多重图与 Dijkstra，平价边稳定选 ID，支持平行边。

点仅吸附最近端点节点（含阈值边界，平局按节点 ID），吸附距离独立记录、未计入行程成本。断网、未吸附、零成本同节点均进入 OD；零长度行程没有线几何。输出 `routes.gpkg`、`service.gpkg` 和 OD/最近设施/覆盖记录。服务区是沿方向的可达路段，不能解释为等时圈面。OSM 高架/桥梁/隧道应依源节点身份保留，不能平面 noding 后冒充原路网。没有转向限制、容量选址最优化或历史交通参数；无路网场景使用下述独立接口。

## 无路网地形成本：`terrain-cost.py`

```bash
$PY gis-kit/scripts/terrain-cost.py dem.tif --friction friction.tif \
  --params walk.json --grass "$EXISTING_GRASS" --output "$RUN/walk"
```

DEM 遵守地形后端的完整、单波段、北向上米制投影格网契约；阻力栅格必须完全同格网，值为非负附加秒/米，**阻力 NoData 明确表示障碍**，DEM 缺值直接拒绝。`start/end:[x,y]` 必须显式位于像元中心，不隐式吸附。另需 `vertical_unit/vertical_datum/source_description`、`walk_coeff:[a,b,c,d]`、负数 `slope_factor`、非负 `friction_lambda/corridor_extra_seconds` 和非空 `cost_assumptions`；可选 `max_pixels` 默认一百万。参数为情景假设，不能由地形推断实测步行速度或安全性。

复用既有 [GRASS r.walk](https://grass.osgeo.org/grass78/manuals/r.walk.html)，八邻域、无骑士步。边成本为 `a*水平距离 + k*有符号高差 + lambda*两端平均阻力*水平距离`；上坡 k=b，中缓下坡 k=c，低于 slope_factor 的陡下坡 k=d。要求 a>0、b/c≥0、d≤0、a+c*slope_factor>0，保证所有坡段有正移动成本。`[.72,6,1.9998,-1.9998]`、断点 -.2125、零阻力下，10m 水平距离上升1m=13.2s、下降1m=5.2002s、下降3m=13.1994s。Horn 坡度图为邻域诊断，寻路使用逐边高差。障碍排除像元节点；对角接触允许，不能解释为具有宽度的连续通行边界。

`from_start` 为起点到各像元成本；`to_end` 在**反号 DEM** 上从终点运行相同算法，得到转置边成本，避免用普通终点出发成本错误代替各像元到终点的成本。`excess_seconds=d(start,v)+d(v,end)-d(start,end)`；走廊为其不超过预声明附加秒数的像元，限定为离散图上允许经过该点的行走路线集合。不可达输出 `reachable=false`、null 成本、空路径 GPKG 和全 NoData 走廊；同起终点为零成本、单像元路径记录、无零长度线。

输出累计成本、回溯方向、超额成本、走廊、坡度/阻力 GeoTIFF、`routes.gpkg` 和记录。路径逐边独立复算，正向总成本与转置场互验；数值容差 1e-7s + 1e-10 相对量级，非业务容差。文件逐值/几何/CRS/单位回读，隔离后端工作目录自动清理。永久手算、Bellman–Ford、障碍/不可达回归见 `test_terrain_cost.py`；真实源 DEM 文件链使用 `run_terrain_cost_acceptance.py`，起终点、坡度阻力和障碍均标为演示假设。

## 密度与空间关联：`spatial-inference.py`

```bash
$PY gis-kit/scripts/spatial-inference.py points.gpkg --params inference.json \
  --backend-python "$EXISTING_PYSAL_PYTHON" \
  --backend-site-packages "$EXISTING_PYSAL_SITE" \
  --backend-proj-data "$EXISTING_PROJ_DATA" --output "$RUN/inference"
```

共用 `id/study/analysis_crs/operation`；输入为有效二维点，稳定 ID 排序后才构建权重。`study` 必须声明研究区与来源选择偏差。

- `operation=infer`：`value/radius_m/permutations/seed`；4—2000 个对象、19—9999 次置换、非负且非常数有限指标、至多 100 万邻接项。距离半径包含边界、不含自身。PySAL 全局和局部 Moran 采用行标准化权重；Gi* 使用二元权重及自身单位权重。全局检验随机标签，局部条件置换；报告 `min(1, 2*p_sim)` 双倍折叠尾概率，分别对局部 Moran 与 Gi* 使用 Benjamini–Yekutieli 多重检验调整。全局包含孤岛但空间滞后为零；局部孤岛及 Gi* 方差未定义值不进入相应推断族。四象限符号自身没有显著性。输出统计 GPKG、权重 IDs/邻居 JSON 和说明。
- `operation=kde`：`bandwidth_m/resolution_m/bounds`，其中 bounds 是分析 CRS 的 `[xmin,ymin,xmax,ymax]`。复用 sklearn Gaussian KDE，单位是记录数/km²。所有输入点须在显式范围内；最多 100 万输出像元。无边界校正、无裁后重新归一化，边界外核尾遗漏，外侧栅格可多出不足一像元。不能解释为人口密度或显著热点；不需要 PySAL 后端。

推断前提为给定研究区/固定权重下的随机标签或条件置换可交换性；脚本无法从观测值证明此前提。目录筛选、年份不一致、测量机制和邻域选择须写入 study；结果仅为所声明空模型下的探索性统计。已知成对同值/交替结构的 Moran I=+1/-1 另有永久回归，不能据此验证真实目录的代表性。

本机验证了既有 PySAL esda 2.3.1/libpysal 4.5.1/NumPy 1.20.1。单独子进程禁用 JIT，HOME/缓存全部隔离至输出邻接临时目录后清理。适配器仅在进程内将旧 `crand.boolean` 设为 NumPy bool，并为 esda 2.3.1 空邻域置换返回数学上为零的局部空间滞后；不更改任何安装文件或权重。其他对象走原库函数。随机种子只保证已验证版本/执行方式复现，不保证跨架构或 JIT 字节一致。

## 适宜性：`suitability.py`

```bash
$PY gis-kit/scripts/suitability.py objects.gpkg --params scenarios.json --output "$RUN/scores"
```

`id/study/criteria/scenarios` 必填。criteria 为名称到 `{field,low,high,prefer:high|low}` 的映射；采用预声明线性尺度并截断至 0..1。scenarios 为 `{id,weights,threshold}` 数组（1—100 个），每个方案显式给全部指标的有限非负权重，归一化后加权。任一指标缺失即排除，即使该指标权重为零。

可选 `constraints:[{field,min,max}]` 是硬约束；`restriction` 是限制区多边形文件路径，任何相交（含边界接触）剔除整个对象，不自动裁剪。输出每对象—方案分数、指标贡献、排除原因、阈值入选、竞争名次，以及稳定入选/永不入选和名次/分值范围。候选对象几何保留，无隐式连续面插值；只检查所提供情景，稳定不能证明真实适宜。`scenario_comparisons` 以第一个情景为基线，记录归一化权重/阈值变化与分数、名次、新增/失去入选的对象 ID；分数变化阈值 1e-12，名次/入选精确比较。`data_insufficient_ids` 保留缺指标对象；全套分数与几何仍在 scores.gpkg。距离、格网、匹配或配准变化必须先产生相应新输入再重跑，现有报告不声称测试这些未提供的输入变化。

## 水系诊断与非线性研究

`hydro-review.py rivers.gpkg streams.tif --params policy.json --output DIR` 使用冻结 `id/spacing_m/tolerance_m/min_matched_fraction/rationale`，复用沿线采样，计算参考样点至正值候选河网像元中心的距离。范围外计为不匹配并 hold；百分比是等间距采样近似，不能冒充精确长度占比。仅邻近性通过不等于流域/上下游一致。`trace_d8` 按 GRASS 的 D8 方向码追踪，显式记录边界、洼地、缺失与循环；它不重新推导流向。真实 Next_down 方向检查及地图在 `tests/run_hydro_consistency_acceptance.py`，任何端点歧义保留 hold。

```bash
$PY gis-kit/scripts/nonlinear-georef.py source.png gcps.json --params policy.json \
  --backend-gdalwarp "$EXISTING_GDALWARP" --output "$RUN/tps"
```

复用仿射控制点/图框/来源哈希契约，至少 6 fit + 4 冻结 check，保留仿射基准；TPS 通过 GDAL 原生控制点转换。额外 policy 明确 `grid_step_px/max_inverse_error_px/max_roundtrip_px/max_condition/resolution/resampling/rationale`。逆向误差与诊断间距为**源图像素**，输出分辨率为目标 CRS 单位。输出双空间残差、采样 Jacobian/折叠与条件数、图框叠加。抽样无折叠不构成全域证明，check 点禁止后验重挑；hold 不发布 TIFF。

实际重采样调用明确的既有 `gdalwarp -tps -et 0`，关闭 0.125px 近似（Rasterio 1.4.3 `reproject` 的 GCP 路径不能用普通 ERROR_THRESHOLD 参数关闭它）。永久回归在非半像元平局位置按独立逆变换核对最近邻源像素，严格零误差；只允许目标格网 1e-9 像元以内的浮点范围表达误差。源框/孔洞以 alpha 掩膜，不修改原图。可选 `--pixel-vectors sampled.gpkg --pixel-manifest export.json --vector-step-px 1` 交接已由原标注导出器沿曲线采样的像素候选；先检查原图/GPKG 哈希与 `sampling_tolerance_pixels`，再按源像素步长加密线段并 TPS 转换，绝不变换贝塞尔控制柄。仅接收完全位于有效图框内的对象，跨框、孔洞穿越、空/无效对象列为遗漏；保留面孔洞、属性、未解决像素问题与原源副本。vectors.gpkg 逐几何/属性/CRS 回读，相对路径交接包可移动。原曲线采样容差和弦长属于源像素约束，不保证世界空间曲线逼近误差；地面/历史真实性保持未知。

资源中 SELF RSS 是本进程高水位，CHILDREN 是已结束子进程的最大 RSS；均不声称并发进程树峰值。测试没有相同口径性能改善结论。
