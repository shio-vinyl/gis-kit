# CAD 图纸清点与候选 CRS

`cad.py inspect` 在把 DXF 当 GIS 数据用之前先看清楚它：有哪些图层、各层是什么实体、文字写了什么、图纸单位、范围里有没有离群的图框，以及坐标量级对得上哪些投影坐标系。结果发布到一个新目录。这里只清点，不写几何，也不给数据赋 CRS。

```bash
python cad.py inspect 控规图则.dxf --output /work/cad/plan
python cad.py inspect 地形图.dxf --near 117.2,31.8 --output /work/cad/topo   # 剔除离图纸所在地太远的候选
python cad.py inspect 旧图.dxf --encoding CP936 --output /work/cad/old      # 文件头缺代码页时
python cad.py inspect 法图.dxf --reference 前海范围.shp --output /work/cad/fatu   # 与已知 CRS 的参考图层比对
```

- **输入**：只读 ASCII 或二进制 DXF。DWG 直接拒绝，先在 AutoCAD / 中望 CAD 里另存为 DXF，或用 ODA File Converter 转换。读模型空间实体，图纸空间（布局）实体单独计数，不参与范围和 CRS 判断。
- **文件头**：自行解析 `$ACADVER`（换算成 R12、R2000 等版本）、`$INSUNITS`（图纸单位）、`$DWGCODEPAGE`（代码页）和 `$EXTMIN/$EXTMAX`；二进制 DXF 不解析文件头。文件头里的范围常常过时，只照原样记下，不用于判断。
- **文字编码**：GDAL 和 ezdxf 都按 `$DWGCODEPAGE` 转码，`ANSI_936` 的中文能正确读出。缺代码页或写成 `ANSI_1252` 时，GBK 中文会被当成 Latin-1 读成乱码；检测到这种情况会给出警告，用 `--encoding CP936` 重跑。
- **两个读取器**：几何、范围、检查图和 CRS 判断始终来自 GDAL（图块引用展开成几何）。图层清点在装了可选依赖 ezdxf 时由 ezdxf 完成，按 CAD 的口径计数：一个图块引用算一个实体。没装 ezdxf 时清点退回 GDAL，`reader.missing` 列出缺少的能力。ezdxf 读不了而 GDAL 能读时，同样退回 GDAL 并给出警告；ezdxf 修复过文件结构时，警告里记下错误和修复的条数。
- **清点（ezdxf）**：按图层统计 DXF 实体类型（`LINE`、`ARC`、`TEXT`、`MTEXT`、`INSERT`、`HATCH`、`DIMENSION` 等），多段线分成 `LWPOLYLINE closed` / `open`（`POLYLINE` 同样）。图块引用记下块名和次数，块属性记下标签和至多 5 个取值。TEXT / MTEXT 记录数量和至多 5 条样例，每条同时给原文和清洗后的文字：清洗去掉 MTEXT 格式码、把换行并成空格、全角字母数字转半角。另外记录图层的关闭 / 冻结状态、图层表里有但模型空间没有实体的图层（`unused_layers`，不含 `0` 和 `Defpoints`），以及各布局的实体数和视口数。
- **清点（GDAL 退回）**：按图层统计实体数、几何类型；R2000 及以上版本还统计 SubClasses 实体类型（如 `AcDbPolyline`、`AcDbHatch`，R12 没有这一信息）。文字样例只有原文。图块已展开，不报告块名；块属性、闭合状态、图层状态和布局都没有。
- **离群实体**：实体中心距中位中心超过中位距离 20 倍的算离群，常见于放在原点的图框或误画的零星对象。按图层列出数量，检查图用红叉标出；范围分别记 `full`（全部）与 `core`（去掉离群），CRS 判断用 `core` 的中位中心。

## 候选 CRS

按代表点的坐标量级判断，然后用 pyproj 的 EPSG 库查出具体坐标系，再用各坐标系的适用范围核对一遍，范围外的列入 `rejected`。

- **带带号的高斯-克吕格坐标**：东坐标为 3 度带 25–45 带或 6 度带 13–23 带的带号 ×10⁶ 加上 500000 左右，直接定出带号。
- **不带带号的坐标**：东坐标在 0–10⁶ 之间。这样的坐标可能是不带带号的高斯坐标、UTM，也可能是城市或项目独立坐标系（例如深圳独立坐标），单看坐标分不出来，所以**不列任何 EPSG**，状态为 `hold`，给了 `--near` 也一样。要确定坐标系，只能和已知 CRS 的参考图层比对（`--reference`，见下文），或用控制点拟合。
- **经纬度**：x、y 落在经纬度范围内时，列 EPSG:4490 和 EPSG:4326。
- **Web 墨卡托**：只在上面都不匹配、坐标不属于无带号的情况，且给了 `--near` 时，才列 EPSG:3857。
- **其他**：都对不上时按局部或工程坐标处理，状态为 `hold`，需要控制点配准。

## 参考图层比对

`--reference`（可加 `--reference-layer`）给一个覆盖同一片区、CRS 已知的图层，它必须是以米为单位的投影坐标系。比对只用范围：把图纸主体范围（`core`）按每种单位换算后与参考图层的范围对照，结论记在 `crs.reference.comparisons[]`，每条含 `overlap`（较小的范围落在较大范围里的比例）、`offset`（参考中心减图纸中心，单位米）、`size_ratio` 和 `verdict`：

- `extents-overlap`：`overlap` 不小于 0.5。参考图层的 CRS 列为候选（`basis: reference`），不带带号的坐标也一样，这是它们得到 EPSG 的唯一途径。
- `zone-prefix`：东坐标正好相差一个带号 ×10⁶（`zone`、`drawing_has_prefix`），去掉或补上带号后范围重叠。按参考 CRS 的基准换算出带号或中央经线对应的坐标系（`twin`）列为候选，例如参考为 EPSG:4526（38 带）而图纸不带带号时，候选为 EPSG:4547（中央经线 114°E）。
- `shift-or-rotation`：两个中心的坐标量级相同但范围不重叠，多半是独立坐标或移过原点。不列候选，记下偏移，应改用控制点拟合。
- `different-magnitude`：量级不同，不是同一个坐标网。

范围对得上只说明与参考的坐标网一致，不能证明就是它：独立坐标系的原点可能刚好落在附近，基准也分不出来。赋 CRS 之前要叠加共有的地物（道路、宗地角点）核对。`record.json` 记下参考图层的路径、图层名和 SHA-256（单文件时）。

## 距离过滤

给了 `--near LON,LAT`（图纸所在地的大致经纬度）时，反算位置离它超过 `--near-km`（默认 50 km）的候选移入 `rejected`，并记下距离。

单靠坐标分不出 CGCS2000、新北京、西安 80 和北京 54 四种基准，候选按这个顺序全部列出，基准要向数据提供方确认，或用已知点核对。`$INSUNITS` 不是米时（如毫米），按声明的单位换算后再判断一次；声明为毫米、坐标却已经符合米制坐标网时会给出警告。所有匹配都只是候选，`crs.assigned` 始终为 `null`。

## 输出

- `cad.json`（`report: cad-inventory`，`schema_version` 1）：`source`（文件名、大小、SHA-256、修改时间），`header`，`reader`（`inventory` 为 `ezdxf <版本>` 或 `gdal`，`geometry` 为 GDAL 版本，`missing` 为退回时缺少的能力），`entities`（`model_space` 为清点口径的实体数，`geometries` 为展开图块后的几何数，以及图纸空间、空几何的数量和几何类型统计），`layers[]`（按实体数降序；ezdxf 时为 `entities`、`dxf_types`、`blocks`、`attributes`、`texts`、`text_samples[{raw, clean}]`、`state`、`bounds`，GDAL 退回时为 `entities`、`geometry_types`、`entity_types`、`texts`、`text_samples[{raw}]`、`bounds`），`unused_layers`，`layouts[]`（`name`、`entities`、`viewports`；GDAL 退回时这两项为 `null`），`extent`，`crs`（`status`、`representative_point`、`near`、`readings[]`，每个 reading 含 `scale_to_metre`、`basis` 和 `matches[]` 的 `grid` 与 `candidates[]`；`rejected`、`reference`（未给 `--reference` 时为 `null`）、`notes`、`assigned`），以及 `warnings`。
- `check.png`：按图层着色（前 12 层，其余为灰色），有离群实体时左侧加一幅全图，用虚线框标出主体范围。坐标轴为图纸单位。
- `record.json`：参数、输入 SHA-256、输出文件摘要与假设。

输出目录必须是新的。确认 CRS 后，用 `convert.py` 转成 GPKG 时坐标系仍为空；赋 CRS 另行处理，不能用 `--crs` 代替，`--crs` 是重投影。
