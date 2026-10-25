# 分析成果进入 Composer

分析原件保留在 gis-kit 一侧。分界原则：**几何、数值和语义归 gis-kit，颜色、纹理和图例归 Composer**，数据只从 gis-kit 流向 Composer。字段、单位、分母、分类边界、NoData 与 `candidate` / `hold` 写在分类方案和 `record.json` 里；这些记录不构成自动审批。

区位逐级放大图所需的各级行政区另走 [admin-chain](admin-chain.md) 约定。交给 Composer 的显示数据有三种：

- 显示 GeoJSON（`convert.py`）。
- 分类方案 `scheme.json`：约定 `class-scheme` v1，Schema 见 [class-scheme.schema.json](class-scheme.schema.json)。
- 不上色的显示栅格 `raster.json`：约定 `display-raster` v1，Schema 见 [display-raster.schema.json](display-raster.schema.json)。

两份约定都由 gis-kit 产出、维护，并在 `gis.py capabilities --json` 的 `contracts` 中声明写出版本；gis-composer 声明读取版本，gis-plugin 的 doctor 负责配对。读取方遇到不认识的 `schema_version` 会拒绝。

## 矢量与机读探查

```sh
"$PY" "$GIS_SKILL/scripts/inspect-data.py" analysis.gpkg --layer means summary --json
"$PY" "$GIS_SKILL/scripts/inspect-data.py" analysis.gpkg layers --json
"$PY" "$GIS_SKILL/scripts/stats.py" describe analysis.gpkg --layer means --json
"$PY" "$GIS_SKILL/scripts/convert.py" analysis.gpkg --layer means --crs EPSG:4326 --output display.geojson
```

上述三个 `--json` 入口 stdout 为纯 JSON，未知统计为 `null`，失败非零退出且诊断走 stderr。其余探查/统计命令与 `indicators.py` 仍沿用原输出，不宣称全部机读接口已统一。显式选择图层；转换后回读原值、空值和状态，不用字符串前缀解析分析报告。

## 分类方案

分类方案只说明每一类是什么，不含颜色，由 Composer 按主题上色，所以换主题时矢量和栅格会一起变色。矢量字段用 `scheme.py` 生成：

```sh
"$PY" "$GIS_SKILL/scripts/scheme.py" analysis.gpkg --layer means --field mean --params scheme-params.json --output scheme
```

```json
{"breaks":[1000,2000,3000,4000],"labels":["< 1,000 m","1,000–<2,000 m","2,000–<3,000 m","3,000–<4,000 m","≥ 4,000 m"],"unit":"m","status":"candidate","nodata_label":"未知 / NoData"}
```

- 分级方式：
  - 数值字段可以直接给 `breaks`，也可以用 `"method":"quantile"` 或 `"equal_interval"` 加 `"classes":k` 计算断点，方法会记录在方案里。断点塌缩时报错，不悄悄合并。
  - 类别字段给 `categories`：可以是 `"unique"`，也可以是 `[{code, label, label_en?, group?}]`。数据里出现了未列出的类别值时报错，除非显式设置 `allow_unlisted`。
- 方案记录每一类的计数（供直方图图例使用）和 NoData 计数。区间下限包含、上限不含，首末区间无界。
- 输出目录里另有一份 `record.json`，记录输入哈希与参数。

## 栅格显示派生

先用 `raster.py warp` 显式转换到 EPSG:3857，声明 `kind`、`resampling` 和目标格网；分类数据禁止双线性插值。原始分析栅格保持不变。`display` 仅接受 north-up EPSG:3857、已归一化 scale/offset 的单个显式波段；不把任意 CRS 四角拉伸为正确图像。

**交给 Composer：`"mode":"values"`**，参数与分类方案相同，不带颜色。

```json
{"band":1,"mode":"values","breaks":[1000,2000,3000,4000],"unit":"m","status":"candidate"}
```

```sh
"$PY" "$GIS_SKILL/scripts/raster.py" display warped.tif --params display-params.json --output display
```

输出四个文件：`display/values.png`、`display/scheme.json`、`display/raster.json` 和 `record.json`。

- **编码**：`encoding` 二选一。
  - `class-index`（默认）：类别序号写在 R + 256×G，渲染时用最近邻采样。
  - `value-rgb24`：配合 `"kind":"continuous"`，数值写成 offset + scale × 24 位整数，渲染时线性插值；`raster.json` 会记录量化误差。
- **NoData**：两种编码都用 alpha 0 表示 NoData，NoData 不能当作零。
- **在 Composer 中引用**：Scene 写 `{"type":"display-raster","path":"…/display/raster.json"}`，Composer 校验图像哈希后按主题上色；图层图例由分类方案自动生成。

**独立使用与检查图：`"mode":"colors"`（默认）**，参数里给 `colors`，每个区间一个 `#RRGGBB`。输出已经上好色的 `display/display.png`，可以直接作为 Scene 的 `type:"image"` source；分类图建议 `paint:{"raster-resampling":"nearest","raster-fade-duration":0}`，图例要在 Scene 里按相同断点和颜色手写。这条路径适合没有 Composer 的场合；交给 Composer 时用 values 模式，颜色才会跟着主题走。

两种模式都有 `max_pixels` 上限，默认 50,000,000 像元，不承诺流式内存。栅格与 PNG 写出并回读核对之后才发布目录；失败时清理 staging，不覆写输入或已有成果。

## 共用 Composer 项目工作区

Composer 的命令只在项目工作区内读写：除 `describe` 与 `project init` 外都必须用 `--project <dir>` 选定项目，产物固定写入 `runs/<run-id>/`，不接受 `--out` 等输出路径参数，越出项目的路径会被拒绝。gis-kit 的成果因此直接交到项目里：

- **分析输入**：用 `_delivery.bundle(<project>/inputs/<分析名>)` 暂存分析文件与显示资源，写好 `record.json` 后原子改名发布。已有目录不覆盖。
- **Scene**：放在项目的 `scenes/`，用 `../inputs/<分析名>/...` 相对引用分析文件。
- **顺序**：先发布分析输入，再调用 Composer 的 `lint` 与 `qa`。不要在 staging 期间渲染：staging 目录发布后就不存在了，Composer 的 `run.json` 会记下失效路径，`rerun` 也无法复核。

```sh
node "$GIS_COMPOSER_HOME/src/agent-entry.js" project init /absolute/project --name "项目名"
# gis-kit 发布到 /absolute/project/inputs/<分析名>/ 之后：
node "$GIS_COMPOSER_HOME/src/agent-entry.js" --project /absolute/project lint scenes/map.scene.json --expect-no-frame
node "$GIS_COMPOSER_HOME/src/agent-entry.js" --project /absolute/project qa scenes/map.scene.json --name draft
```

Composer 的 `run.json` 为每个读取的输入记录项目内相对路径和 SHA-256，算法与 gis-kit `record.json` 的 `files` 摘要相同，所以可以逐个核对从分析到成图的溯源。两份记录各管各的：gis-kit 的 `record.json` 只说明分析输入集已完整发布，成图是否完成看 Composer 运行记录的 `status`。分析的候选状态单独保留，正式采用仍需人工判断。不要把这个顺序扩展为 workflow engine。

## 验收示例

永久示例 `tests/run_composer_acceptance.py` 使用本地真实高程样区：原始像元中心统计 → 诊断网格均值 GPKG/GeoJSON 与显示重投影 → 同一套断点的矢量分类方案和不上色的显示栅格 → 用 research、atlas、presentation 三套主题各渲染一张矢量图和一张栅格图，共六张 Composer 专题图。网格是构造的统计单元，未知列没有有效像元，不声称为行政边界。示例面向勃朗峰周边、单位米的样区，其他数据须调整图题、单位与断点。

```sh
PYTHONDONTWRITEBYTECODE=1 "$PY" "$GIS_SKILL/tests/run_composer_acceptance.py" \
  /absolute/local-elevation-sample.tif /absolute/new-run-directory \
  --composer "$GIS_COMPOSER_HOME" --source-description '实际来源及未核验项'
```

示例调用真实 CLI，并检查以下各项：
- 在新输出目录里用 `project init` 建项目，分析输入发布到 `inputs/mont-blanc grid/`，从项目外的 cwd 渲染。
- 核对 Composer `run.json` 中来自 `inputs/` 的每个文件，其哈希与 gis-kit `record.json` 一致。
- 把整个项目搬到带空格的目录后，用 `rerun` 复核输入哈希并逐字节比对 PNG。
- 只改样式时，分析文件哈希不变。
- 矢量方案与栅格方案的断点、标签一致，且都不含颜色。
- 每套主题下，图例的类别标签、计数和颜色来自分类方案与主题色谱，矢量图与栅格图一致；三套主题的颜色互不相同。
- 用独立的 contains 判断回算网格均值；每套主题取五个内部像元，像元颜色必须等于该主题色谱中对应类别的颜色。
- 记录页面资源，确认没有隐式底图。
- 缺资源、越出项目的路径、QA 未通过、使用已移除的输出路径参数，都必须失败并给出对应错误代码（`SOURCE_FILE_MISSING`、`PATH_OUTSIDE_PROJECT`、`IMPORTANT_LABEL_MISSING`、`OUTPUT_FLAG_REMOVED`），失败的运行不得记为 ok。

display 两种模式"写出失败不发布"的保证由底层测试注入部分 PNG 写出失败来覆盖；`tests/test_composer_display.py` 另覆盖 values 模式的类别序号、24 位数值往返、类别方案与拒绝条件，以及 `scheme.py` 的分级方法、计数和 NoData。外部自有文件无需 Composer 专属 record，状态也不会被自动升级。

实际打开六张 PNG，检查中文长标题、十二个网格标签、图例、未知列、无默认边框。精确原生字形框仍 unavailable；自动 QA 通过不证明精确零冲突。日志和 Composer 输出 JSON 中的绝对路径属于历史证据；搬移后的复核依靠项目内的相对路径（Scene 资源与 `run.json` 输入）。
