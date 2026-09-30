# 配方、对象图册、候选组面与仿射交接

复用已验证的 `daily.py`、`plot.py`、`raster-annotate.py` 和 `raster-georef.py`。所有新入口的 `--output` 必须是不存在的目录；整包暂存、回读后发布，拒绝覆盖。输入不修改，候选与几何验收不会自动变成历史正确性。

## 顺序配方：recipe.py

已经执行过的固定 CLI 调用可由 `gis.py --trace` 记录，再以 `recipe.py from-trace` 生成本节的 v1 草稿；不替换执行器，不自动接受 candidate。具体选择、指纹和拒绝边界见 [Harness](harness.md)。

```bash
python3 scripts/recipe.py recipe.json --cache /work/cache --output /work/delivery
python3 scripts/recipe.py recipe.json --bindings replacements.json --cache /work/cache --output /work/repeated
```

版本 1 使用 JSON；输入路径相对配方文件解析，替换文件为角色→路径对象，例如 `{"study":"/data/other.gpkg"}`。研究区作为普通输入角色替换。

```json
{
  "schema_version": 1,
  "inputs": {"study": "/data/area.gpkg"},
  "steps": [
    {"id":"grid", "operation":"grid", "input":"study",
     "params":{"size_m":100,"origin":[0,0]},
     "validate":{"min_rows":1,"crs":"EPSG:32650"}},
    {"id":"profile", "operation":"profile", "input":"grid", "params":{}}
  ],
  "variants": [
    {"id":"fine"},
    {"id":"coarse","params":{"grid":{"size_m":200}}}
  ]
}
```

未声明 `runner` 的步骤保持 `daily.py` 核心操作兼容，不执行任意 shell、插件或外部服务；`input`/可选 `right` 引用输入角色或更早步骤。ID 仅允许英文字母、数字、下划线与短横线。具体前置条件沿用各操作（CRS、稳定 ID、规则、单位等）；`validate` 支持 `min_rows/max_rows/columns/crs` 输出要求。`params` 保留完整操作参数，变体仅浅层替换指定步骤参数；嵌套值应整体给出。不自动生成无界笛卡尔积。

缓存键包含输入内容、上游签名、全部参数（含 CRS/精度）、验证要求、脚本实现和实际已安装 Python 包版本。更改上游参数会使依赖步骤失效；无关步骤仍可命中。保守实现指纹覆盖整个 scripts 目录，可能多重算，不声称最小化失效或加速。缓存命中也检查记录封印、产物哈希及真实文件回读；缺失或损坏会重算。缓存是本机可信目录，不是对恶意共同写者的安全边界。

失败只保留已验证的缓存步骤，最终成果目录不发布；修复参数后用同一缓存、全新输出恢复。`hold` 步骤阻止整条配方发布，不自动采用清理候选或越过规则失败。成功包保留各步骤原始记录与 `recipe.json/record.json`。配方/缓存不运行并发；锁防合作写者冲突，进程被强制终止后的残留锁须先确认没有活跃写者，再由调用者清理，不能盲删。已补充下述固定栅格/专业操作适配；仍不含复杂 DAG、远程调度或任意脚本步骤。

### 栅格与专业操作适配

步骤增加 `runner`、`files`、`artifact`；无需新配方版本。省略 runner 仍走 daily，禁止任意脚本名与 shell。下例接收 `dem`、`zones` 输入角色：

```json
{"id":"classes","runner":"raster","operation":"reclassify",
 "files":{"inputs":[{"input_ref":"dem"}]},
 "params":{"rules":[{"min":0,"max":3000,"output":1},{"min":3000,"max":10000,"output":2}]},
 "artifact":"result.tif","validate":{"min_valid_pixels":1}}
```

随后可用 `{"input_ref":"classes"}` 引用所选产物。`files` 中每个文件及 params 中 vector/reference/table/exclude/protected/scope/restriction、profile.input 等文件参数必须通过 `input_ref` 引用输入角色或更早步骤，不能藏入未指纹追踪的路径。参数变体同样追踪这些引用；研究区替换使相应统计及其下游失效，无关步骤保持可复用。

固定适配器与 files：

| runner | operation | files |
|---|---|---|
| raster | `raster.py --help` 支持的操作 | `inputs` 为 input_ref 数组 |
| terrain / terrain-detail | run | input |
| terrain-backend | hydrology / viewshed | input、grass |
| terrain-cost | run | input、friction、grass |
| network | run | input、origins、facilities；source_vertices 模式另传 access-areas、barriers |
| suitability | run | input；restriction 放 params 的 input_ref |
| spatial-inference | run；实际 kde/infer 放 params.operation | input；可选 backend-python |

`grass`、`backend-python` 也是已存在文件的输入角色，不安装后端。推断可另设步骤级 `backend:{"backend-site-packages":"/existing/site-packages","backend-proj-data":"/existing/proj"}`，仅允许这两个已存在绝对目录。后端契约沿用原 CLI；不继承业务通过状态。GRASS、推断及显式 backend 的步骤每次重算，刻意禁用缓存命中：启动器内容不足以证明整套外部模块未变，不冒充完整后端锁文件。它们的新产物仍封印、回读并参与下游内容指纹。普通 raster/terrain/专业本进程步骤继续缓存。

`artifact` 是需传给下游的顶层文件名，默认 result.tif；可以选择 table.json、slope.tif、scores.gpkg 等真实输出，拒绝路径穿越。缓存封印覆盖整包文件，含辅助修补图、贡献波段、分类表；任一文件缺失、增加或损坏都重算。命中时逐 TIFF 解码、逐 GPKG 图层回读、JSON 解析。外部 mask/aux/overview 在配方读入时即拒绝，避免缓存命中绕过 CLI 拒绝。

适配器 validate 支持 min_valid_pixels/crs，GPKG 支持 min_rows/max_rows/columns；规则类型与所选产物必须一致。专业输出 candidate 必须显式 `allow_candidate:true`；hold 不能绕过。整条配方 complete 仅说明执行完成，各步骤原始 candidate/unknown 保留在 record 和 dossier.json。档案含任务适用条件、有效范围、修补/插补数与上游角色；类别转换后的插补身份需沿角色回查修补步骤，不能将其重新命名为观测。

recipe 根记录明确 `status_scope:execution_only`；dossier 的 `process_status=complete` 仅描述执行，原始状态另在 `source_status`，`source_status_present` 区分未声明与显式值。旧字段 `execution_status` 保留原源状态的兼容别名语义，不改成进程状态；缺失时也不再补 complete。未声明状态的旧栅格结果保持未声明；显式 null/unknown/hold 不可通过 adapter，candidate 仍须布尔值 `allow_candidate:true`。直接上游状态与 daily 的 input/right 角色一并保留。

## 对象档案与 HTML/PNG 图册：atlas.py

```bash
python3 scripts/atlas.py objects.gpkg --params atlas.json --output /work/atlas
```

参数：`{"id":"object_id","layer":"parcels","group":"district","analysis_crs":"EPSG:32650","span_m":2000,"k":3,"max_objects":200}`。`id` 必填；其余按需提供。默认使用共享计量模块确定投影；不同对象采用同一方形投影范围，`span_m` 太小导致截断时拒绝。比例尺标明投影距离，HTML 打印缩放不构成固定纸面比例尺。

按可选分组、稳定 ID 排序；文件用零补齐序号，原始 ID 保存在清单和档案，斜线等不会成为路径。每个对象都有 JSON 档案或显式遗漏原因。空/无效/Z/M 几何保留属性及遗漏，不伪造地图；缺 CRS、重复 ID 或无法可靠投影会拒绝任务。对象档案含来源哈希、属性、位置、平面面积/长度和 K 近邻；业务异常未验证。

PNG 页面复用现有 `plot.py`，统一图例、范围规则、选中对象标注和投影比例尺，附最多四个属性及面积/长度摘要表；长文本截断有省略号，完整值保存在 JSON/HTML。`index.html` 提供全字段表、邻近关系及相对引用；不是 PDF/QGIS Atlas 后端。渲染完成后应实际看地图、孔洞、字体、摘要表与范围，不能把文件存在当作视觉验收。普通地图绘制不启用新增表格布局。

## 已确认线网候选组面：candidates.py

```bash
python3 scripts/candidates.py /work/annotation-run --confirmed confirmed.json --output /work/faces
```

`confirmed.json` 必须包含 `schema_version:1`、原图 `source_sha256`、当前 revision 文件的 `revision_sha256`、`reviewer/evidence`、明确的 `confirmed_edges` ID 列表和 `frames:[{"id":"main","pixel_region":{...Polygon...}}]`。可选 `tolerance` 是原始像素空间贝塞尔采样容差。审批绑定当前原图和版本；不根据距离猜测连接。

各图框独立组面；插图不可与主图面积重叠，应在主图 Polygon 中排除插图孔洞。仅可见且明确确认的有效边进入处理，跨框边、不确定/推测边和自交边列入遗漏。几何交叉或重合端点缺共享节点 ID 时保留疑义并排除相关边，既不 noding 也不 snapping，包括已声明不连接的空间交叉。几何独立分量可以继续产出候选。

复用 [Shapely polygonize_full](https://shapely.readthedocs.io/en/stable/reference/shapely.polygonize_full.html)，输出闭环、孔洞、cut edges、dangles 和 invalid rings。每个候选环回映有向共享边 ID，并重建回读几何一致性；保留原生贝塞尔与共享节点。内环闭合区域也可能独立成为相邻候选面，不能据此自动判定它是湖、飞地还是其他政区。

`candidates.json` 与 `pixel-features.json` 均为像素诊断 JSON；后者虽含 FeatureCollection 结构，仍不是 RFC 7946 地理 GeoJSON，不可按 EPSG:4326 导入。结果始终 `hold`，不命名政体、不修改标注、不自动采用。完整图幅仍须依据覆盖台账和独立原图审核，合成拓扑测试不替代真实源图的同色内部界、飞地/孔洞、插图和遗漏验收。

## 仿射版本比较与交接：georef-delivery.py

```bash
python3 scripts/georef-delivery.py compare old-transform.json new-transform.json --output /work/displacement
python3 scripts/georef-delivery.py package handoff.json --output /work/spatial
```

比较要求同一源图、图框 ID/有效区域、投影 CRS，且独立检查点 ID、像素和目标坐标固定。报告双版检查残差、验收状态和整个图框的最大仿射位移；相同投影 CRS 下，该位移范数最大值在多边形顶点取得。改变检查点、图框或 CRS 会拒绝比较，不把换控制点后更低残差直接当作更准确。

`handoff.json`：`{"schema_version":1,"image":"/data/source.png","pixel_gpkg":"/work/pixel-candidates.gpkg","pixel_manifest":"/work/export.json","transforms":["/work/main.json","/work/inset.json"]}`。使用绝对路径；`allow_hold:true` 仅用于显式诊断。输入必须是未定义 CRS 的二维像素几何；活动 SQLite sidecar 和未绑定的外部栅格掩膜/aux/overview 拒绝，独立图框不允许面积重叠。

复用已有 fit/check 门槛、源图哈希、半像素规则和逐框矢量筛选，整包发布未重采样 GeoTIFF、掩膜、GPKG、残差预览、原变换及交接清单。逐块回读原始像素/图框掩膜、CRS、变换、矢量几何与属性；跨框/框外对象逐帧记录遗漏。源图和像素导出副本在 `source/`，输出与像素导出引用相对定位，可搬移交付目录；原始绝对路径仅作为来源记录。`raster-georef.py preview` 增加图框、fit 凸包与外推边界轮廓，保留原有残差连线。

全部几何门槛通过也只发布 `spatial_candidate`；显式允许的未通过配准保持 `hold`。原始像素几何不重写。不涉及 TPS、多项式、重采样变形或 D 阶段专业算法。
