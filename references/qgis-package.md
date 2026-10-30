# 打包成带样式的 GeoPackage（QGIS）

`package.py pack` 读一份 [`map-style`](https://github.com/shioo-gis/gis-plugin/blob/main/contracts/README.md#map-style) v1（一张图的图层、数据引用、绘制顺序和样式，与平台无关）和它引用的数据，写出一个 GeoPackage。同事在 QGIS 里打开包里的工程，看到的颜色、类别、标注位置与 gis-composer 出的 PNG 一致；单独把某张表拖进 QGIS，也带着这张图的样式。

```bash
python package.py pack maps/example.map-style.json --output /work/send/example
python package.py pack maps/example.map-style.json --output /work/send/example-clip --clip --data-root basemap=/data/basemap
```

`map-style` 由 gis-composer 从发布版导出（也可以来自 gis-view），gis-kit 只负责转成平台格式。转换按“map-style → 某个平台”的输出器组织：`_map_style.py` 读取、校验和解析数据路径，与平台无关；`_qgis_style.py` 只管 QGIS（QML、表达式、工程、`layer_styles`）。以后加 ArcGIS Pro（`.lyrx`、`.lpkx`）时另写一个输出器，取数部分照用。

## 数据范围

图幅取 `extent.bbox`（经纬度，已含余量），换到每份数据自己的坐标系里再选，四边加密后再转换，投影后的弯边不会漏掉要素。`--margin 10%`（或 `0.1`）先把图幅按宽高各外扩这个比例。

| 档位 | 参数 | 矢量 | 栅格 |
|---|---|---|---|
| 按图幅选取 | 默认 | 保留和图幅相交的完整要素，不切几何；面积、容积率等字段仍然对得上 | 取覆盖图幅的窗口 |
| 裁切 | `--clip` | 沿图幅裁切，加 `clipped` 字段标出被切过的要素；裁切后只保留与图层同维度的部分 | 同上 |
| 整份 | `--full` | 原样整份 | 整幅 |

- `map-style` 没有 `extent` 时只能用 `--full`，否则报错。数据没有 CRS 时无法按图幅选取，同样报错。
- 源数据已有 `clipped` 字段时，`--clip` 报错，不覆盖。
- 所有数据保持原坐标系，不转成渲染用的 Web 墨卡托。

## 数据路径

`data.path` 的解析方式与 gis-composer 一致（同一个项目，两边读到同一份文件）：

- **`dataRoot: "project"`**（默认）：相对项目根，即 `map-style` 文件所在目录或其上级中含 `gis-project.json` 的那一级；也可以用 `--project <目录>` 指定。找不到时报错。
- **`dataRoot: "file"`**：相对 `map-style` 文件所在的目录。
- **`@<根名>/…`**：外部数据根（底图、DEM 等不进项目的共享数据）。按顺序查：`--data-root 名=路径`（可重复），本机配置 `roots.json`（`$GIS_COMPOSER_CONFIG_DIR`，否则 `$XDG_CONFIG_HOME/gis-composer` 或 `~/.config/gis-composer`，与 gis-composer 共用），`geo-assets` 还可以取 `GEO_ASSET_ROOT`。找到了项目时，根名必须在 `gis-project.json` 的 `external` 里声明。查不到就报错，并说明去哪里配，不猜路径。
- 绝对路径、`~` 和 `..` 一律拒绝；`@根名/…` 也不能越出这个根。
- `data.sha256` 与文件不符时报错（数据在导出 `map-style` 之后改过），请重新导出。文件夹形式的数据（FileGDB）不核对哈希，记入清单。
- GDAL 读不了的数据（例如一个瓦片文件夹）不中断打包，列入清单的 `not_packed`，引用它的图层不进工程。

## 写出什么

新目录（原子发布，已存在就报错）里有：

- **`<id>.gpkg`**（GeoPackage 1.2，旧版 QGIS 自带的 GDAL 也能读）：
  - **每份数据一张表**。同一份数据被多个图层引用（面、描边、白色衬边）时只存一份，多个样式指向它。表名取图层名或文件名。
  - **栅格**：1～4 波段 Byte（无 nodata）和单波段 Int16、UInt16、Float32 存成 GeoPackage 栅格表（PNG 或 TIFF 瓦片，无损）；其余类型（含带 nodata 的 Byte）存成包旁的 `rasters/<名>.tif`，工程用相对路径引用，清单里写明原因。
  - **`layer_styles`**：每个矢量图层一条 QML 样式，`styleName` 为图层 id。每张表的第一个（最下层的）样式设为默认，QGIS 添加这张表时自动套用；其余样式在“图层属性 → 样式 → 从数据库加载”里可选。栅格的样式只在工程里（QGIS 只为矢量表读 `layer_styles`）。
  - **`map_labels`、`map_label_leaders`**：`labelPlacements` 写成的点图层和引线图层，见下文。
  - **`qgis_projects`**：内嵌的 QGIS 工程，名称为 `map-style` 的 id（或 `--name`）。
- **`package.json`**（`manifest: qgis-package` v1）：清单，见下文。
- **`record.json`**：运行记录，含本机绝对路径；发给同事时只需 `.gpkg`（和 `rasters/`）与 `package.json`。

在 QGIS 里打开：**项目 → 从…打开 → GeoPackage**，或在浏览器面板展开这个 GeoPackage，双击里面的工程。工程中的数据源写成相对 GeoPackage 文件的路径（`./<id>.gpkg|layername=…`），**不要给 `.gpkg` 改名**，否则工程找不到图层（单独添加表不受影响）。

## 内嵌工程

- 图层顺序按 `layers`（自下而上）写成 QGIS 的自定义绘制顺序；图层面板按 `group` 分组（ground、groundLines、fills、lines、reference、points、labels），组内和组间都是上层在前。分组不连续时也不影响绘制顺序。
- 图层名取 `title`，其次 `legend.title`，再次图层 id；`visible: false` 的图层在面板里不勾选。
- 工程坐标系取“主要图层”的 CRS：底图（ground、groundLines）之外的图层里用得最多的那个，平局取更靠上的图层；只有底图时才看底图。
- 工程范围设为图幅（换到工程坐标系）；没有图幅时取所有数据范围的并集。

## 样式怎么转

尺寸一律 px → mm（1 px = 1/96 英寸 = 0.2646 mm），颜色 `#RRGGBB` 加透明度写成 QGIS 的 `r,g,b,a`。

| map-style | QGIS |
|---|---|
| `single` | 单一符号（singleSymbol） |
| `categorized` | 分类（categorizedSymbol）。`visible: false` 的类不画；`default` 写成值为空的“其他所有值”类（未匹配的值和 NULL） |
| `graduated` | 分级（graduatedSymbol）。map-style 是左闭右开、最后一档右闭；QGIS 两端都闭、先匹配先用，所以除最后一档外，每档上限取下一档下限之前最近的浮点数，恰好落在分界上的值进上一档（与 PNG 一致）。有 `default` 时改写成规则渲染（QGIS 分级没有兜底档），清单里注明 |
| `rule` | 规则（RuleRenderer）。map-style 取第一条命中的规则，QGIS 会画所有命中的规则，所以每条规则再排除前面的规则；`default` 写成“前面都不命中”的规则，`null` 不画 |
| `renderer: null` | 不画要素（nullSymbol），只出标注 |
| 面的 `fill` / `stroke` | 简单填充 + 一层简单线作轮廓（虚线、线帽、拐角都保留） |
| 线的 `stroke` | 简单线；`dash` 写成自定义虚线，奇数个数值按 SVG 规则重复一遍 |
| 点的 `marker` | 简单标记：circle、square、triangle、diamond；`ring` 写成空心圆（描边取 `stroke`，没有时取 `fill` 的颜色、1 px） |
| `size`（比例圆） | 标记直径的数据定义表达式：`linear` 为线性，`sqrt` 为 `r0 + (r1 − r0)·(√v − √d0)/(√d1 − √d0)`；`clamp` 缺省按 `true`（与 gis-composer 用的 MapLibre 一致）；值为半径，乘 2 成直径 |
| `sortBy` | 要素绘制顺序（order by），`asc` 先画小值 |
| `opacity` | 图层不透明度 |
| `filter` | 写成父规则包住各类（渲染和标注都包），样式本身就带着筛选，单独添加这张表时也一样 |
| 栅格 `pseudocolor` | 单波段伪彩色；`linear`、`discrete`、`exact` 对应插值、离散、精确。`discrete` 每个颜色从它的值用到下一个值之前。`stops` 的值按数据值写入，`stretch` 不重算，清单里注明 |
| 栅格 `palette` | 调色板（paletted） |
| 栅格 `rgb` | 多波段彩色，按打包窗口的最小、最大值拉伸 |

**筛选转 QGIS 表达式**：字段写成 `"字段"`，字符串单引号转义；`all`、`any`、`not` 转成 `AND`、`OR`、`NOT`。NULL 的处理和 MapLibre 一致：缺值不等于任何值，所以 `!=`、`notIn` 对 NULL 为真，比较运算对 NULL 为假（不是“未知”）。`{"expression": …, "dialect": "qgis"}` 原样使用；其他方言按约定当作“没有筛选”，并列入清单：图层筛选就是不筛，规则的筛选就是这条规则接住余下的全部要素。

**标注**：

- 图层上的 `labels` 写成 QGIS 原生标注：`field` 直接取字段，`template` 的 `{字段}` 转成 `concat(…)` 表达式；字体、字号、颜色、晕边（缓冲）照写。`placement`：点 `auto` 为环绕点、`point` 为点上；线 `auto` 为平行、`line` 为沿线弯曲；面为质心偏移，`polygon-center` 要求落在面内。`enabled: false`（gis-composer 导出时都是这样）时标注设置保留但关闭，在图层属性里打开即可用。
- `labelPlacements` 写成 `map_labels` 点图层（EPSG:4326，就是给出的经纬度），每个标注一个点，字段有 `text`、`font`、`size_px`、`size_mm`、`color`、`halo_color`、`halo_px`、`halo_mm`、`anchor`、`quadrant`、`rotation`、`layer`、`feature_id`、`has_leader`。样式是不画点、只出标注，字体、字号、颜色、晕边、锚点（象限）和旋转都由字段数据定义，允许重叠、总是显示，与 PNG 一致，也能用标注工具逐个拖动。`rotation` 按顺时针度数（与 MapLibre `text-rotate` 相同，QGIS 的数据定义旋转也是顺时针）。
- 带 `leader` 的标注另写到 `map_label_leaders` 线图层（`label_id` 对应 `map_labels`），用标注的文字颜色、1 px 画在标注下面。

## 清单

`package.json`：

- `map_style`：文件名、SHA-256、id、标题、产出方；`scope`、`margin`、`extent`（实际使用的图幅和原图幅）。
- `geopackage`：文件名、大小、工程名、表；`project`：坐标系、范围、打开方法。
- `datasets[]`：`ref`（map-style 里的写法）、`root`（`project`、`file` 或 `@根名`）、`table`、类型、坐标系、档位、源要素数与打包要素数、`--clip` 时被切的要素数、源数据大小与 SHA-256、引用它的图层；栅格另有存储方式、尺寸、窗口。
- `layers[]`：每个图层的表、map-style 渲染方式、写成的 QGIS 渲染器、是否默认样式、标注开关、筛选表达式。
- `label_placements`：标注点和引线的数量。
- `fonts[]`：用到的字体（图层标注、`labelPlacements`、`fonts[]`）及用在哪里。同事电脑上缺字体时 QGIS 会换成别的字体，`warnings` 里会提示。
- `unsupported[]`：转不了或做了近似的地方（图层、项目、原因），例如不认识的筛选方言、线图层上的 `marker`、虚线的点标记轮廓、栅格的 `stretch`。
- `not_packed[]`：没打包的数据和原因。
- `bytes`、`warnings`：包超过 500 MiB、用了外部数据根（发出前请确认许可）、栅格存成 GeoTIFF 等都会提示。

## 还没做的

- 版式、图例、比例尺、标题（QGIS `.qpt`）不导出；`legend`、`extent.panels`、`extent.print`、`scaleDenominator` 只记录不使用。
- 不写 SLD（`styleSLD` 为空）；只写 QML。
- 瓦片底图不能原样打包：有矢量源时请在 `map-style` 里引用矢量源，否则列入 `not_packed`。
- 原生标注（`labels`）由 QGIS 自己排，位置和 PNG 不一定相同；要和 PNG 一致请用 `map_labels`。

## 验证

`tests/test_package_pack.py` 用虚构数据覆盖三档范围、四种矢量渲染方式、比例圆、虚线、筛选、标注点层与引线、共享数据表、栅格两种存储、数据根与哈希报错、未打包数据和未支持项。装有 QGIS 时（macOS 默认路径，或设 `GIS_KIT_QGIS_PYTHON` 为 QGIS 自带的 Python），还会用 QGIS 打开包里的工程，核对图层有效、渲染器类型、类别颜色、绘制顺序、单独添加表时的默认样式，并在渲染图上取色（`tests/qgis_open_check.py`）。
