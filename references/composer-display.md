# 分析成果进入 Composer

分析原件保留在 gis-kit 一侧；Composer 只消费显示 GeoJSON 或明确四角的 PNG。字段、单位、分母、分类边界、NoData 与 `candidate` / `hold` 在既有 Scene 的 `analysis` 自定义字段或分析 `record.json` 中明确记录；这些记录不构成自动审批。样式留在 Scene，不新增交接协议或第二套样式模型。

## 矢量与机读探查

```sh
"$PY" "$GIS_SKILL/scripts/inspect-data.py" analysis.gpkg --layer means summary --json
"$PY" "$GIS_SKILL/scripts/inspect-data.py" analysis.gpkg layers --json
"$PY" "$GIS_SKILL/scripts/stats.py" describe analysis.gpkg --layer means --json
"$PY" "$GIS_SKILL/scripts/convert.py" analysis.gpkg --layer means --crs EPSG:4326 --output display.geojson
```

上述三个 `--json` 入口 stdout 为纯 JSON，未知统计为 `null`，失败非零退出且诊断走 stderr。其余探查/统计命令与 `indicators.py` 仍沿用原输出，不宣称全部机读接口已统一。显式选择图层；转换后回读原值、空值和状态，不用字符串前缀解析分析报告。

## 栅格显示派生

先用 `raster.py warp` 显式转换到 EPSG:3857，声明 `kind`、`resampling` 和目标格网；分类数据禁止双线性插值。原始分析栅格保持不变。`display` 仅接受 north-up EPSG:3857、已归一化 scale/offset 的单个显式波段；不把任意 CRS 四角拉伸为正确图像。

```json
{"band":1,"breaks":[1000,2000,3000,4000],"colors":["#edf8b1","#a1dab4","#41b6c4","#2c7fb8","#253494"],"unit":"m","status":"candidate"}
```

```sh
"$PY" "$GIS_SKILL/scripts/raster.py" display warped.tif --params display-params.json --output display
```

输出 `display/display.png` 与既有 `record.json`。`coordinates` 为 NW/NE/SE/SW 的经纬度，可直接用于 Scene 的 `type:"image"` source；对应 layer 使用 `type:"raster"`，分类图建议 `paint:{"raster-resampling":"nearest","raster-fade-duration":0}`。像元颜色与图例从相同断点/颜色生成，区间下限包含、上限不含，首末区间无界。有效零保留，NoData/非有限值透明，不能当作零。透明底下显示什么颜色由 Scene 决定，并在图例注明未知。`record.json` 含原栅格信息、源哈希、波段、单位、状态、分类计数、像素回读与 PNG 哈希。

这是有 `max_pixels` 上限的全量显示路径，默认 50,000,000 像元；不承诺流式内存。栅格与显示 PNG 的写出/回读完成后才发布目录，失败清理 staging，不覆写输入或既有成果。

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

永久示例 `tests/run_composer_acceptance.py` 使用本地真实高程样区：原始像元中心统计 → 诊断网格均值 GPKG/GeoJSON 与显示重投影 → 分级 PNG → 两张 Composer 专题图。网格是构造的统计单元，未知列没有有效像元，不声称为行政边界。示例面向勃朗峰周边、单位米的样区，其他数据须调整图题、单位与断点。

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
- 用独立的 contains 判断回算网格均值；取五个内部像元，与最终地图颜色核对。
- 记录页面资源，确认没有隐式底图。
- 缺资源、越出项目的路径、QA 未通过、使用已移除的输出路径参数，都必须失败并给出对应错误代码（`SOURCE_FILE_MISSING`、`PATH_OUTSIDE_PROJECT`、`IMPORTANT_LABEL_MISSING`、`OUTPUT_FLAG_REMOVED`），失败的运行不得记为 ok。

display 自身"写出失败不发布"的保证由底层测试注入部分 PNG 写出失败来覆盖。外部自有文件无需 Composer 专属 record，状态也不会被自动升级。

实际打开两张 PNG，检查中文长标题、十二个网格标签、图例、未知列、无默认边框。精确原生字形框仍 unavailable；自动 QA 通过不证明精确零冲突。日志和 Composer 输出 JSON 中的绝对路径属于历史证据；搬移后的复核依靠项目内的相对路径（Scene 资源与 `run.json` 输入）。
