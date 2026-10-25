# 场地的行政区链

`admin-chain.py` 按场地找出各级行政区：每一级取包含场地的那个单元，再取它下一级的全部单元作为内部界线。结果发布到一个新目录，交给 Composer 做区位逐级放大图。这里只取单元、按显示比例简化，不画界、不合并，也不审定边界。

```bash
python admin-chain.py --source gadm_410-levels.gpkg --point 2.2945,48.8584 --levels 0,1,2,3 \
  --site-label Site --output /absolute/project/inputs/paris
python admin-chain.py --source gadm_410-levels_admin0_context.gpkg --point 113.42,23.10 --levels 0,1,2 \
  --adm0-layer ADM_0_cn_review_full --labels 中国,广东省,广州市 --site-label 场地 --output /absolute/project/inputs/site-chain
python admin-chain.py --source gadm_410-levels.gpkg --site site.gpkg --levels 0,1,2 --output /absolute/project/inputs/site-chain
```

- **输入**：每级一个图层（默认 `ADM_{level}`，可用 `--layer-pattern` 改），字段为 GADM 的 `GID_n` / `NAME_n`，CRS 为 EPSG:4326。场地给 `--point LON,LAT`，或给 `--site` 面文件（任意 CRS，取并集；用代表点找单元，场地超出最末一级时记警告）。
- **找单元**：每级必须恰好有一个单元覆盖场地点，0 个或多个都直接失败，不猜。下一级的 `GID_{n-1}` 必须等于上一级所选单元；只有第 0 级换成 `--adm0-layer` 时，不一致只记警告（例如审图口径的 ADM0 与 GADM 下级的国家代码不同）。
- **名称**：默认 `NAME_n`；`--name-field NL_NAME` 先取本地名，GADM 写成 `NA` 的值视为缺失，`a|b` 取第一个。`--labels` 逐级给显示名，个数必须与 `--levels` 相同。
- **下级界线**：除最末一级外，每级记下其下一级（`--levels` 中的下一项）里属于它的全部单元，按 `GID` 排序，上限 5000 个。下级图层没有对应的 `GID_n` 字段时只记警告。
- **显示简化**：每级几何按该级范围的 1/`--detail`（默认 2500）做共边简化，坐标取到 1e-6 度。成果只供显示，不能拿去量面积或做叠加分析。
- **输出**：`chain.geojson`（各级单元与场地，属性 `key` 为 `adm0`…`site`）、`context.geojson`（下级单元，属性 `parent` 为所属级别）、`chain.json`（约定 `admin-chain` v1）与 `record.json`。目录必须是新的；同一输入两次运行，两份 GeoJSON 逐字节相同。

`chain.json` 记录：`site`（`kind` 为 `point` 或 `area`、`label`、代表点 `point`、`bounds`），`levels[]`（`key`、`level`、`layer`、`gid`、`name`、`label`、`bounds`、`children` 的级别与数量），`files.chain` / `files.context`（路径、SHA-256、要素数），`source`（文件名、`attribution`、大小与修改时间、`adm0_layer`），以及 `display.detail` 和 `warnings`。

- **数据口径**：GADM 不是权威边界。各国下级区划的新旧不一，例如中国的 ADM_3 把广州市区几个区合成了一个单元，名称为 `NA`。要用其他区划时，换成字段相同的 GeoPackage。国界口径由 `--adm0-layer` 显式选择并写进清单；出图时在来源行注明。

Composer 侧把两份 GeoJSON 当普通数据放进 sheet 组合：每级一个面板，范围取 `chain.json` 里该级的 `bounds`，用 `focus` 块突出下一级（按 `key` 过滤）、`context.lines` 画本级轮廓与下级界线（按 `parent` 过滤），再选 `row`、`hero` 等版面结构。示例见 Composer 仓库 `examples/sheets/paris-chain.sheet-composition.json`；颜色由主题决定。
