# 按图概括的显示矢量

`display-vector.py` 为一张图的视角生成显示用的面、陆上国界线和海岸线。小比例尺下直接用详细边界（如 GADM）会出两个问题：成千上万个小岛，以及把面的轮廓当国界描，海岸和每个小岛都被描成国界。这里不预存多比例尺数据，而是只为这一张图的范围和比例尺概括一次，结果很小，可以缓存。

```bash
python display-vector.py --source gadm_410-levels_admin0_context.gpkg --layer ADM_0_cn_review_full \
  --id-field iso3 --fields display_country_name --view view.json --cache ~/.cache/display-vector \
  --output /absolute/project/inputs/display/europe
python display-vector.py --source countries.geojson --id-field iso3 --fields name,density --keep MLT \
  --view '{"center":[11.5,56.1],"zoom":2.59,"width":733,"height":611}' --output /absolute/project/inputs/display/eu-members
python display-vector.py --source plots.gpkg --layer plots --fields landuse --profile print --dpi 300 \
  --view view.json --output /absolute/project/inputs/display/plots-print
```

- **视角**：`--view` 是一段 JSON 或 JSON 文件，含 `center`（经纬度）、`zoom`、`width`、`height`，可选 `tileSize`（默认 512）。按 Web 墨卡托、512 像素瓦片换算像素大小，与 MapLibre 的 fit 一致；gis-composer 的 `inspect` 给出每张图每个面板的 `view`，原样传入即可。读取范围在视角外各加 `--margin`（默认 5%）。
- **输入**：EPSG:4326 的面（GeoPackage 图层或 GeoJSON）；`--fields` 列出要保留的属性。
- **id**：`--id-field` 在视角内必须唯一，写在面上，也是国界两侧 `a`、`b` 的值。只用来填色的面（如地块）常常没有唯一字段，可以不给：此时用源数据的要素 id（FID）作 id，字段名 `fid`，GeoPackage 从 1 起、GeoJSON 从 0 起。FID 不随视角变（读入位置会变），同一要素在各张图里 id 相同，重复运行逐字节一致；但它只在这份源数据里唯一，不能拿来挂接别的数据。清单 `source.id_field` 为 `fid`、`source.id_generated` 为 `true`，`record.json` 的 `assumptions` 里写明。`--keep` 要按名字点出单元，必须给 `--id-field`，否则报错；`--fields` 里有 `fid` 时也要求把它作为 `--id-field` 给出。
- **概括**（全部在 Web 墨卡托米制下进行，像素处处等大）：
  1. 先删去小于 `--min-area-px`（屏幕默认 4）平方像素的部件，它们是大部分顶点，也不与别的单元共边；
  2. 按 `--tolerance-px`（屏幕默认 0.5）像素做共边简化，相邻单元的公共边保持重合；
  3. 简化后再按同一阈值删一次，整个单元都过小时记入 `units_dropped` 并写警告；`--keep` 列出的单元保留其最大部件（如欧盟地图里的马耳他）；
  4. 陆上国界：每对相邻单元取一次公共边，允许亚像素的缝隙或重叠，并去掉紧贴海岸的端头；
  5. 海岸线：所有面合并（闭合亚像素缝隙）后的外轮廓，去掉视角裁剪边框。
- **印刷图**：按厘米排版、按 dpi 出图的地图用 `--profile print`，见文末「印刷图」一节。
- **坐标**：取到约 1/20 像素的小数位（最多 6 位），网格取整后结果逐字节可重复。
- **输出**：`areas.geojson`（单元，带 id 与保留属性）、`boundaries.geojson`（陆上国界，属性 `a`、`b` 为两侧 id）、`coast.geojson`、`display-vector.json`（约定 `display-vector` v1）与 `record.json`。目录必须是新的；同一输入、参数与视角两次运行，三份 GeoJSON 逐字节相同。
- **缓存**：`--cache` 目录按源文件标识（名称、大小、修改时间）、参数与视角取键；命中时直接复制结果（`record.json` 的 `cache.status` 为 `hit`），首次为 `miss`。

`display-vector.json` 记录 `view`（含 `meters_per_pixel` 与读取范围 `bbox`）、`display`（档位 `profile`、印刷档的 `dpi`、容差、最小面积、边距、坐标精度）、`files`（路径、SHA-256、要素数）、`counts`（源与显示顶点数、删去的部件数、`units_dropped`、`kept_small`、国界与海岸段数）、`source`（文件名、图层、id 字段与是否生成的 `id_generated`、保留字段、`attribution`、大小与修改时间）和 `warnings`。

- **只供显示**：不能拿这些几何量面积、做叠加或分析。删去哪些单元写在清单里，必须出现的用 `--keep`。
- **比例尺变了就重做**：Composer 在地图清单里用 `context.display` 引用底图包、用编码块的 `data` 引用专题包，编译时核对哈希，lint 比较包的视角与实际渲染：缩放差超过 0.1 报 `DISPLAY_VECTOR_SCALE_MISMATCH`，画面超出包的范围报 `DISPLAY_VECTOR_EXTENT`。
- **口径**：国界口径取决于源数据，例如 GADM 的 `ADM_0_cn_review_full`；概括不改变口径，只改变细节程度。

## 印刷图

这里的像素都是视角的像素，即 `--view` 的 `width`、`height` 所用的 CSS 像素（1/96 英寸）。屏幕上一个 CSS 像素就是一个设备像素，默认的半像素容差和 4 平方像素最小面积正好看不出删改。Composer 按厘米排版的地图（地图清单 `layout.widthCm`）以 `layout.dpi`（或项目 `print.dpi`，默认 300）出图，每个 CSS 像素是 dpi/96 个印刷像素。纸上一个 CSS 像素约 0.26 mm，4 平方像素约 0.3 mm²，而 300 dpi 的印刷像素只有约 0.085 mm：屏幕档会删掉纸上还看得见的小地块、小绿地。

`--profile print` 把同一条规则用在印刷像素上：容差 0.5 × 96/dpi，最小面积 4 × (96/dpi)²，换回视角像素写进清单。`--dpi` 取地图实际的出图 dpi（只在 `--profile print` 下可用，96–960）：

| dpi | `--tolerance-px` | `--min-area-px` |
| --- | --- | --- |
| 屏幕（`--profile screen`，默认） | 0.5 | 4 |
| 150 | 0.32 | 1.6384 |
| 300（默认） | 0.16 | 0.4096 |
| 600 | 0.08 | 0.1024 |

显式给出的 `--tolerance-px`、`--min-area-px` 优先于档位，例如 `--profile print --min-area-px 1` 把最小面积定为 1 平方像素，容差仍取印刷档。容差越细保留的顶点越多，印刷档的包比屏幕档大。印刷图的 `inspect` 报告的 `view` 仍是 CSS 像素，原样传入即可，dpi 另用 `--dpi` 给出。

**与 Composer lint 一致**：Composer 的 `VECTOR_DETAIL_TOO_FINE` 用的也是这组阈值，并按画布的 `pixelRatio`（按厘米出图时为 dpi/96）缩放：同一 dpi 的厘米图配印刷档的包不会触发警告，比印刷档还细的数据仍会。
