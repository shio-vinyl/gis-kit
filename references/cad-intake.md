# CAD 图纸清点与候选 CRS

`cad.py inspect` 在把 DXF 当 GIS 数据用之前先看清楚它：有哪些图层、各层是什么实体、文字写了什么、图纸单位、范围里有没有离群的图框，以及坐标量级对得上哪些投影坐标系。结果发布到一个新目录。这里只清点，不写几何，也不给数据赋 CRS。

```bash
python cad.py inspect 控规图则.dxf --output /work/cad/plan
python cad.py inspect 地形图.dxf --near 117.2,31.8 --output /work/cad/topo   # 无带号坐标，给出大致位置
python cad.py inspect 旧图.dxf --encoding CP936 --output /work/cad/old      # 文件头缺代码页时
```

- **输入**：只读 ASCII 或二进制 DXF。DWG 直接拒绝，先在 AutoCAD / 中望 CAD 里另存为 DXF，或用 ODA File Converter 转换。读模型空间实体，图纸空间（布局）实体单独计数，不参与范围和 CRS 判断。
- **文件头**：自行解析 `$ACADVER`（换算成 R12、R2000 等版本）、`$INSUNITS`（图纸单位）、`$DWGCODEPAGE`（代码页）和 `$EXTMIN/$EXTMAX`；二进制 DXF 不解析文件头。文件头里的范围常常过时，只照原样记下，不用于判断。
- **文字编码**：GDAL 按 `$DWGCODEPAGE` 转码，`ANSI_936` 的中文能正确读出。缺代码页或写成 `ANSI_1252` 时，GBK 中文会被当成 Latin-1 读成乱码；检测到这种情况会给出警告，用 `--encoding CP936` 重跑。
- **清点**：按图层统计实体数、几何类型；R2000 及以上版本还统计实体类型（如 `AcDbPolyline`、`AcDbHatch`，R12 没有这一信息）。文字实体（TEXT / MTEXT，以及 R12 中带文字的点）记录数量和至多 5 条样例，填充图案名不算文字。图块引用由 GDAL 展开成几何，不报告块名。
- **离群实体**：实体中心距中位中心超过中位距离 20 倍的算离群，常见于放在原点的图框或误画的零星对象。按图层列出数量，检查图用红叉标出；范围分别记 `full`（全部）与 `core`（去掉离群），CRS 判断用 `core` 的中位中心。

## 候选 CRS

按代表点的坐标量级判断，然后用 pyproj 的 EPSG 库查出具体坐标系，再用各坐标系的适用范围核对一遍，范围外的列入 `rejected`。

- **带带号的高斯-克吕格坐标**：东坐标为 3 度带 25–45 带或 6 度带 13–23 带的带号 ×10⁶ 加上 500000 左右，直接定出带号。
- **不带带号的坐标**：东坐标在 0–10⁶ 之间，中央经线无法确定，状态为 `hold`。给出 `--near LON,LAT`（图纸所在地的大致经纬度）后，列出相应 3 度带和 6 度带中央经线的高斯-克吕格坐标系，以及该处的 UTM 带，只保留反算位置在 `--near-km`（默认 50 km）以内的候选。城市或项目独立坐标系的坐标也是这个样子，用前要和控制点核对。
- **经纬度**：x、y 落在经纬度范围内时，列 EPSG:4490 和 EPSG:4326。
- **Web 墨卡托**：只在上面都不匹配且给了 `--near` 时才列 EPSG:3857。
- **其他**：都对不上时按局部或工程坐标处理，状态为 `hold`，需要控制点配准。

单靠坐标分不出 CGCS2000、新北京 54、西安 80 和北京 54 四种基准，候选按这个顺序全部列出，基准要向数据提供方确认，或用已知点核对。`$INSUNITS` 不是米时（如毫米），按声明的单位换算后再判断一次；声明为毫米、坐标却已经符合米制坐标网时会给出警告。所有匹配都只是候选，`crs.assigned` 始终为 `null`。

## 输出

- `cad.json`（`report: cad-inventory`，`schema_version` 1）：`source`（文件名、大小、SHA-256、修改时间），`header`，`entities`（模型空间、图纸空间、空几何的数量，以及几何类型统计），`layers[]`（按实体数降序；`entities`、`geometry_types`、`entity_types`、`texts`、`text_samples`、`bounds`），`extent`，`crs`（`status`、`representative_point`、`near`、`readings[]`，每个 reading 含 `scale_to_metre`、`basis` 和 `matches[]` 的 `grid` 与 `candidates[]`；`rejected`、`notes`、`assigned`），以及 `warnings`。
- `check.png`：按图层着色（前 12 层，其余为灰色），有离群实体时左侧加一幅全图，用虚线框标出主体范围。坐标轴为图纸单位。
- `record.json`：参数、输入 SHA-256、输出文件摘要与假设。

输出目录必须是新的。确认 CRS 后，用 `convert.py` 转成 GPKG 时坐标系仍为空；赋 CRS 另行处理，不能用 `--crs` 代替，`--crs` 是重投影。
