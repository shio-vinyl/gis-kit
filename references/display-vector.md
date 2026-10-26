# 按图概括的显示矢量

`display-vector.py` 为一张图的视角生成显示用的面、陆上国界线和海岸线。小比例尺下直接用详细边界（如 GADM）会出两个问题：成千上万个小岛，以及把面的轮廓当国界描，海岸和每个小岛都被描成国界。这里不预存多比例尺数据，而是只为这一张图的范围和比例尺概括一次，结果很小，可以缓存。

```bash
python display-vector.py --source gadm_410-levels_admin0_context.gpkg --layer ADM_0_cn_review_full \
  --id-field iso3 --fields display_country_name --view view.json --cache ~/.cache/display-vector \
  --output /absolute/project/inputs/display/europe
python display-vector.py --source countries.geojson --id-field iso3 --fields name,density --keep MLT \
  --view '{"center":[11.5,56.1],"zoom":2.59,"width":733,"height":611}' --output /absolute/project/inputs/display/eu-members
```

- **视角**：`--view` 是一段 JSON 或 JSON 文件，含 `center`（经纬度）、`zoom`、`width`、`height`，可选 `tileSize`（默认 512）。按 Web 墨卡托、512 像素瓦片换算像素大小，与 MapLibre 的 fit 一致；gis-composer 的 `inspect` 给出每张图每个面板的 `view`，原样传入即可。读取范围在视角外各加 `--margin`（默认 5%）。
- **输入**：EPSG:4326 的面（GeoPackage 图层或 GeoJSON）；`--id-field` 在视角内必须唯一，`--fields` 列出要保留的属性。
- **概括**（全部在 Web 墨卡托米制下进行，像素处处等大）：
  1. 先删去小于 `--min-area-px`（默认 4）平方像素的部件，它们是大部分顶点，也不与别的单元共边；
  2. 按 `--tolerance-px`（默认 0.5）像素做共边简化，相邻单元的公共边保持重合；
  3. 简化后再按同一阈值删一次，整个单元都过小时记入 `units_dropped` 并写警告；`--keep` 列出的单元保留其最大部件（如欧盟地图里的马耳他）；
  4. 陆上国界：每对相邻单元取一次公共边，允许亚像素的缝隙或重叠，并去掉紧贴海岸的端头；
  5. 海岸线：所有面合并（闭合亚像素缝隙）后的外轮廓，去掉视角裁剪边框。
- **坐标**：取到约 1/20 像素的小数位（最多 6 位），网格取整后结果逐字节可重复。
- **输出**：`areas.geojson`（单元，带 id 与保留属性）、`boundaries.geojson`（陆上国界，属性 `a`、`b` 为两侧 id）、`coast.geojson`、`display-vector.json`（约定 `display-vector` v1）与 `record.json`。目录必须是新的；同一输入、参数与视角两次运行，三份 GeoJSON 逐字节相同。
- **缓存**：`--cache` 目录按源文件标识（名称、大小、修改时间）、参数与视角取键；命中时直接复制结果（`record.json` 的 `cache.status` 为 `hit`），首次为 `miss`。

`display-vector.json` 记录 `view`（含 `meters_per_pixel` 与读取范围 `bbox`）、`display`（容差、最小面积、边距、坐标精度）、`files`（路径、SHA-256、要素数）、`counts`（源与显示顶点数、删去的部件数、`units_dropped`、`kept_small`、国界与海岸段数）、`source`（文件名、图层、id 与保留字段、`attribution`、大小与修改时间）和 `warnings`。

- **只供显示**：不能拿这些几何量面积、做叠加或分析。删去哪些单元写在清单里，必须出现的用 `--keep`。
- **比例尺变了就重做**：Composer 在地图清单里用 `context.display` 引用底图包、用编码块的 `data` 引用专题包，编译时核对哈希，lint 比较包的视角与实际渲染：缩放差超过 0.1 报 `DISPLAY_VECTOR_SCALE_MISMATCH`，画面超出包的范围报 `DISPLAY_VECTOR_EXTENT`。
- **口径**：国界口径取决于源数据，例如 GADM 的 `ADM_0_cn_review_full`；概括不改变口径，只改变细节程度。
