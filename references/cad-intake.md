# CAD 图纸：转换、清点、提取、围面、挂接、赋 CRS 与叠图质检

## DWG 转 DXF

`cad.py convert` 调用本机已装的转换器，把 DWG 转成 DXF，再把结果读回来核对。gis-kit 不解析 DWG，也不负责安装转换器。

```bash
python cad.py convert 法图.dwg --output /work/cad/fatu-dxf            # 自动选择转换器：有 ODA 用 ODA，否则用 LibreDWG
python cad.py convert 法图.dwg --tool libredwg --version r2013 --output /work/cad/fatu-dxf
```

- **转换器**：
  - ODA File Converter：免费，安装时要接受 ODA 的许可协议；程序会在 PATH 和常见安装位置查找。
  - LibreDWG 的 `dwg2dxf`：GPL 许可，`brew install libredwg` 即可安装。2026-10 用 0.14 版实测，能转 AutoCAD 2018（AC1032）的 DWG。
  - 两种都没装时报错，提示安装或在 AutoCAD / 中望 CAD 里另存为 DXF。
- **原件不动**：转换器读的是临时副本，原始 DWG 只读。`--version` 指定输出的 DXF 版本（r2000–r2018，默认 r2018），`--timeout` 指定超时秒数。
- **回读核对**：
  - 从 DWG 文件头读出版本。
  - 用 ezdxf 读回 DXF，统计模型空间实体数、有实体的图层数、图层表大小、图纸空间实体数和范围。
  - 转换器的输出、警告和用时都写进清单。
- **LibreDWG 输出的已知问题**：
  - 有些 XRECORD 的扩展数据里有组码小于 1000 的标签，ezdxf 的恢复模式会拒绝读取。读取时会在临时副本里去掉这些标签，记下去掉的条数；实体不受影响。
  - 输出的 DXF 文件头里，`$DWGCODEPAGE` 可能写成 ANSI_936，见下文"文字编码"。
- **输出**：新目录，含 `<源文件名>.dxf`、`manifest.json`（`manifest: cad-convert` v1：源文件 SHA-256、DWG 版本、转换器与命令、退出码、用时、输出 SHA-256、回读结果、警告）和 `record.json`。

## 清点

`cad.py inspect` 在把 DXF 当 GIS 数据用之前先看清楚它：有哪些图层、各层是什么实体、文字写了什么、图纸单位、范围里有没有离群的图框，以及坐标量级对得上哪些投影坐标系。结果发布到一个新目录。这里只清点，不写几何，也不给数据赋 CRS。

```bash
python cad.py inspect 控规图则.dxf --output /work/cad/plan
python cad.py inspect 地形图.dxf --near 117.2,31.8 --output /work/cad/topo   # 剔除离图纸所在地太远的候选
python cad.py inspect 旧图.dxf --encoding CP936 --output /work/cad/old      # 文件头缺代码页时
python cad.py inspect 法图.dxf --reference 前海范围.shp --output /work/cad/fatu   # 与已知 CRS 的参考图层比对
```

- **输入**：ASCII 或二进制 DXF。DWG 直接拒绝，先用 `cad.py convert` 转换，或在 AutoCAD / 中望 CAD 里另存为 DXF。读模型空间实体，图纸空间（布局）实体单独计数，不参与范围和 CRS 判断。
- **文件头**：自行解析 `$ACADVER`（换算成 R12、R2000 等版本）、`$INSUNITS`（图纸单位）、`$DWGCODEPAGE`（代码页）和 `$EXTMIN/$EXTMAX`；二进制 DXF 不解析文件头。文件头里的范围常常过时，只照原样记下，不用于判断。
- **文字编码**（结果写在 `cad.json` 的 `encoding` 里）：
  - 给了 `--encoding` 时，按所给的编码读（`source: option`）。
  - R2007（AC1021）及以上版本的 DXF 一律是 UTF-8，GDAL 也按 UTF-8 读，不看 `$DWGCODEPAGE`（`source: dxf-version`）。不这样做的话，AutoCAD 或 LibreDWG 写着 ANSI_936 的 R2018 图纸会读成"鍥炬"一类的乱码。
  - 更早的版本按 `$DWGCODEPAGE` 读（`source: header`）。如果图层名或文字看起来像 GBK 被当成 Latin-1 读了（缺代码页、或者写成 ANSI_1252），就依次用 CP936 和 GB18030 重读，取无法解码的字符串最少的那个（`source: auto`，`tried` 记下各自的条数），并给出警告。
  - 用 ezdxf 时，`undecodable_strings` 记录最终仍有字节解不出来的图层名和文字条数。
- **GDAL 读取**：
  - 取消 GDAL 默认的每个图块 10000 个要素的上限，以免大的图块被截断。
  - GDAL 把多面网格（POLYFACE）读成 PolyhedralSurface，shapely 读不了这种几何，因此这类几何不进入范围计算，只计数并给出警告。
  - GDAL 转码时报告的问题也记进警告。
- **两个读取器**：几何、范围、检查图和 CRS 判断始终来自 GDAL（图块引用展开成几何）。图层清点在装了可选依赖 ezdxf 时由 ezdxf 完成，按 CAD 的口径计数：一个图块引用算一个实体。没装 ezdxf 时清点退回 GDAL，`reader.missing` 列出缺少的能力。ezdxf 读不了而 GDAL 能读时，同样退回 GDAL 并给出警告；ezdxf 修复过文件结构时，警告里记下错误和修复的条数。
- **清点（ezdxf）**：按图层统计 DXF 实体类型（`LINE`、`ARC`、`TEXT`、`MTEXT`、`INSERT`、`HATCH`、`DIMENSION` 等），多段线分成 `LWPOLYLINE closed` / `open`（`POLYLINE` 同样，多面网格和多边形网格另记为 `POLYLINE polyface` / `mesh`）。图块引用记下块名和次数，块属性记下标签和至多 5 个取值。TEXT / MTEXT 记录数量和至多 5 条样例，每条同时给原文和清洗后的文字：清洗去掉 MTEXT 格式码、把换行并成空格、全角字母数字转半角。另外记录图层的关闭 / 冻结状态、图层表里有但模型空间没有实体的图层（`unused_layers`，不含 `0` 和 `Defpoints`），以及各布局的实体数和视口数。
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

输出目录必须是新的。

## 湘源控规图纸直取

湘源控规画的图则，地块本身就带着全部属性，可以跳过下面的提取、围面和文字挂接，用 `cad.py xiangyuan` 直接取。需要 ezdxf。

```bash
python cad.py xiangyuan 法图.dxf --units m --max-area-diff 0.005 --output /work/cad/fatu-xy
```

- **湘源怎么存地块**（妈湾法图实测）：
  - 地块边界：`<用地代码>_K` 图层上的闭合多段线（例如 `C1+R2_K`）；填充：`<用地代码>_T` 图层上的 HATCH。
  - 属性：存在边界线的扩展数据里，每个属性单独注册成一个应用名，值放在 1000 组码。数值也存成字符串；空值写成空串，不是省略字段。字段有两套：
    - 湘源 7.0 的地块属性表：`用地名称`、`用地类型`、`第二位比例`、`单位名称` 等。
    - 深圳汇交的字段：`用地性质`、`主要功能代码`、`管控类型`、`删除标记`、`图则编号` 等。妈湾的 `_K` 面上用的是这一套。
  - 副本：地块属性还会被复制到图块定义里的道路红线等实体上，例如妈湾的 `luwang`、`luwang0616` 块。这些副本不是地块。
  - 未炸开的湘源地块是自定义实体 `LZX_LAND`，写进 DXF 时只剩一个类标记，属性全部丢失。
  - 规划控制单元：`用地代码 = DY`。
  - 编号图块：`12 地块编号` 图层上的 `地块编号` 图块，块属性里重复了地块的属性。
  - 配套设施：`配套设施` 图层上的图块，扩展数据里带设施类别、名称、建设状态、所在地块编号。
  - 指标表：图框旁的表格多半是嵌入的 Excel（`OLE2FRAME`），不是文字。妈湾有 4 个，分别显示逐地块表、规划控制单元表（单元面积、建筑容积总量、各用途比例）、城镇单元表和用地平衡表；4 个工作簿的同名表互相不一致。CAD 自己的表格（`ACAD_TABLE`）画成匿名块 `*T…` 里的文字和线。
- **地块（`parcels`）**：每条带 `用地代码` 扩展数据的闭合多段线生成一个面，圆弧按 `--arc-tolerance` 离散。
  - 湘源和深圳字段名单里的扩展数据字段按原名保留（清单 `fields` 列出）；其他插件写的应用（例如 `LHB_YJ`、`HintCAD_*`）合成一个 JSON 字段 `other_xdata`，不丢弃。同一个应用有几个 1000 组码时，按湘源自己的读法取最后一个，并给出警告。
  - 数值字段（`用地面积`、`容积率`、`建筑密度`、`绿地率`、`建筑限高`、各类面积、车位、户数、人数等）中，空串转为空值。整列都能解析成数字时转成数值；只要有一个值解析不了，整列保留原文，并在警告里给出示例值。清单的 `summary.numeric_fields` 列出已转换的字段。
  - `--schema` 可以传入一份 JSON，补充字段名单（`land_attribute_fields_order`、`gis_field_mapping` 的键）和数值字段（`numeric_like`）；清单记下它的 SHA-256。
  - 另加 `parcel_id`（地块编号）、`landuse`（用地代码）、`landuse_name`（用地性质）、`control_type`（`地块` / `规划控制单元`）、`unit_id`、`area_m2`（按几何算）、`area_declared`（`用地面积`）、`area_diff`（相对差）、`number_block`（有没有编号图块落在里面且编号一致）、`fills`（落在面里的 `_T` 填充个数）、`valid`、`layer`、`handle`、`other_xdata`。
  - 出现下列情况时标 hold，并写明原因：
    - 图层既不是 `<用地代码>_K` 也不是 `<用地代码>`；
    - 有删除标记；
    - 没有地块编号；
    - 同类面里编号重复；
    - 几何无效；
    - 给了 `--max-area-diff` 时，面积差超过它。
- **编号图块核对**：编号图块不在任何面里（`number-block-outside`），或者所在面的编号不一致（`number-block-mismatch`）时，写入 `qa_number_blocks`。
- **编号图块补空**：面是权威。编号图块只对应一个面时，块属性里有值、而面上为空或没有的字段（限湘源和深圳字段名单，`地块编号`、`用地代码`、`用地性质` 除外）补到面上，`filled_from_block` 列出补了哪些字段；面上已有的值不改。清单 `summary.filled_from_block` 按字段计数。妈湾补了 4 个 `规划项目编号`（值为 `—`）和 2 个 `备注`。
- **嵌入的 OLE 对象（`ole_frames`）**：每个 `OLE2FRAME`（模型空间、图纸空间或块里）一条记录，需要 olefile（可选）。
  - 位置：取二进制数据头部记的四个角点。LibreDWG 写在组码 10/11 里的是占位值（妈湾 4 个框完全相同），只在头部读不出时才用，`corners_from` 记下来源。只有模型空间的框有几何。
  - 内容：解开 OLE 复合文档，按 `CompObj` 读出类型名（`user_type`）和 ProgID（`prog_id`）。Office 2007 以后的对象把文件原样存在 `Package` 流里，另存为 `ole/<句柄>.xlsx`（或 `.docx`、`.pptx`）；Excel 97 一类存成 `.xls`，其他存成 `.cfb`。清单记文件大小和 SHA-256。
  - 工作表（需要 openpyxl）：`sheets` 列出各表及其范围，`shown_sheet`、`shown_range` 是工作簿保存时的活动表，也就是框里显示的那张表。框的画面本身不渲染。
  - 版本核对：同名工作表在不同工作簿里内容不一样时，清单 `embedded.sheet_versions` 按内容分组，列出各组在哪些框里、在哪个框里显示，并给出警告。不判断哪一份对。
  - 没装 olefile 时照样列出框和位置，不取内容，并给出警告。
- **表格（`tables/`）**：每个 `*T…` 匿名块里的 TEXT / MTEXT 按位置排成行列（间距在字高一半以内的算同一行或同一列），写成 `tables/<块名>.csv`（UTF-8 带 BOM）：`row`、`col`、`text`、`x`、`y`、`layer`、`handle`。合并单元格的文字落在几行或几列中间，会单独占一个行号或列号，按坐标看。清单 `embedded.tables` 记单元格数、行列数、第一行文字、引用它的 `ACAD_TABLE` 及其所在块、这个块被插入了几次。
  - LibreDWG 转出的 `ACAD_TABLE` 只剩一行 `0/ACAD_TABLE`，没有内容（妈湾 `tukuang` 块里那张），ezdxf 读不到。程序按组码计数发现这种情况就给出警告：单元格还能从 `*T` 块读出，但表格和块的对应关系丢了；需要它时用 ODA File Converter 重新转换。妈湾的 `*T58` 是旧版的规划区用地汇总表（居住用地 32.45 公顷），`tukuang` 和 `*T58` 都没有被插入到图里。
- **填充配对**：`<代码>_T` 上的 HATCH 不带属性，只用来核对。清单的 `summary.fills` 列出三项：没有填充的地块数、不在任何地块里的填充数、填充图层代码和地块用地代码不一致的情况。
- **块内副本**：图块定义里带 `用地代码` 的实体不当作地块。清单的 `block_copies` 按块列出这类实体的个数，以及这个块在模型空间里被插入了几次。
- **未炸开的自定义实体**：模型空间里有 `LZX_LAND` 实体时直接报错，提示先在湘源里炸开或输出，再重新转换。CLASSES 段只登记了这个类、却没有实例时照常处理，清单 `custom_entities` 记下这一情况。代理实体只给出警告。
- **单元（`units`）**：`--unit-layer`（默认 `DY范围`）上的闭合面，按落在里面的 `--unit-label-layer`（默认 `DY编号`）文字命名。
  - 没有标签、有多个标签、或者与已有的面重合（不论起点和方向）的，都标 hold。
  - 地块按代表点落在哪个单元来取 `unit_id`。
  - 清单的 `area.units_by_id_m2` 给出各单元面积，可以直接对照说明书。
- **配套设施（`facilities`）、规划边界（`boundary`）**：分别取 `--facility-layer`（默认 `配套设施`）的图块，和 `--boundary-layer`（默认 `----规划边界`）的闭合面。每个设施按它的 `地块编号` 找对应地块，`parcel_match` 记为以下四种之一：`inside`（落在该地块里）、`elsewhere`（在别处，`parcel_distance_m` 记下离该地块的距离）、`unknown id`（编号对不上任何地块）、`no id`（没有编号）。清单记录地块面积在边界内的部分。图层名不一样时用参数指定，传空值就跳过。
- **单位**：`--units m|mm|header` 必填。湘源图纸的 `$INSUNITS` 常写成英寸，实际按米画；用 `header` 时，如果 `用地面积` 只有按米算才对得上，就拒绝执行，并提示改用 `--units m`。清单的 `parameters.units_basis` 记下所用单位和 `$INSUNITS` 原值。
- **输出**：
  - `parcels.gpkg`：`parcels`、`units`、`facilities`、`qa_number_blocks`、`boundary`、`ole_frames`，无 CRS；
  - `ole/`、`tables/`：嵌入文件原样另存，表格单元格；
  - `check.png`：地块按用地大类着色，单元画虚线，hold 的面描橙边，OLE 框画蓝色点线并标出显示的工作表；
  - `manifest.json`（`manifest: cad-xiangyuan` v1）：统计、hold 原因、面积汇总、`embedded`（OLE 框、工作表版本、表格）；
  - `record.json`。
- **CRS**：不设 CRS，之后接 `assign-crs` 或 [矢量控制点转换](vector-georeferencing.md)。
- **指标**：图里没填的指标（例如容积率、地块容积）先看 `ole/` 里的工作簿，再看说明书；按编号挂接见 [按编号挂接指标表](vector-metrics.md#按编号挂接指标表)。
- **单元面积口径**：`area.units_m2` 是 `DY` 面（规划控制单元）的几何面积之和，这些面各自沿用了一个地块编号；和 OLE 单元表里的单元面积（公顷）对账时，以表为准，几何和只作核对。

## 实体提取

`cad.py extract` 把选定图层的模型空间实体写成 `raw.gpkg`，每个要素都带 `layer` 和 `handle`，后续步骤可以追溯到原图实体。需要 ezdxf。

```bash
python cad.py extract 法图.dxf --layers 地块界线,地块编号,用地代码 --units header --output /work/cad/fatu-raw
python cad.py extract 建筑.dxf --units mm --arc-tolerance 0.02 --include-blocks --output /work/cad/building-raw
```

- **图层**：`--layers` 逗号分隔；不给时取所有有模型空间实体的图层。所列图层在模型空间里没有实体时报错，并列出有实体的图层。图纸空间实体不提取，数量写进清单。
- **单位**：`--units` 必填。
  - `m`、`mm`：按所给单位处理。
  - `header`：按 `$INSUNITS` 换算成米。`$INSUNITS` 为无单位或缺失时拒绝；声明为毫米等、坐标却已符合米制坐标网时也拒绝，提示改用 `--units m`。
  - 换算系数和依据写进清单。只做缩放，不移动坐标，也不设 CRS。
- **曲线**：`LINE`、`LWPOLYLINE`、`POLYLINE`、`ARC`、`CIRCLE`、`ELLIPSE`、`SPLINE` 以及多段线的凸度，按 `--arc-tolerance`（弦到曲线的最大距离，单位米，默认 0.05）离散。
- **图层 `lines`**：上述所有曲线，字段 `layer`、`dxf_type`、`handle`、`closed`、`block`、`source_handle`。
- **图层 `closed_polygons`**：闭合的 `LWPOLYLINE` / `POLYLINE` 同时生成面，字段同上，另加 `valid`。无效的面照原样保留、标为 `valid = false`，不修复；不足 3 个顶点的计入 `skipped`。
- **图层 `texts`**：`TEXT`、`MTEXT`、块属性 `ATTRIB` 生成点，字段 `text_raw`、`text_clean`（清洗规则同 inspect）、`layer`、`dxf_type`、`tag`（块属性的标签）、`height`（米）、`rotation`（度）、`anchor`、`handle`、`block`、`source_handle`。设了对齐方式的 TEXT 取对齐点（`anchor = align`），其余取插入点。
- **图块**：
  - 默认不炸开：图块引用写入 `blocks` 图层（插入点、块名、旋转、缩放，`attributes` 为标签到取值的 JSON），块属性另写入 `texts`。
  - `--include-blocks` 炸开图块（含嵌套，最多 16 层）：块内实体按类型写入各图层，`handle` 为空，`source_handle` 为最外层图块引用的句柄，`block` 记录块名路径；块内 0 层的实体沿用图块引用的图层，与 CAD 的显示规则一致。
- **Z 值**：任何坐标的 Z 不为 0 时拒绝；加 `--drop-z` 才压平成二维，清单记录受影响的要素数和 Z 值范围。
- **句柄**：R12 图纸可能不带实体句柄，此时句柄是 ezdxf 读取时分配的，在 CAD 里查不到，清单会给出警告。
- **引线**：
  - `LEADER` 的箭头尖写入 `leaders` 图层（字段 `layer`、`dxf_type`、`handle`、`text_handle`、`linked`）。它标注的 MTEXT 如果也被提取，就改放到箭头处（`anchor = leader`、`arrows = 1`、`leader_handle`）。
  - 一段文字被多条引线指着时，`arrows` 记条数，文字位置不变。
  - `MULTILEADER` 的文字写入 `texts`（`dxf_type = MULTILEADER`）：只有一个箭头时放在箭头处，多个箭头时放在文字位置，`arrows` 记个数；每个箭头都写入 `leaders`。
  - 没连到任何已提取文字的引线 `linked = false`。
- **图层 `hatches`**：每个 `HATCH` 生成它填充的那块面（多面），作为地块边界的备用来源。
  - 边界路径按 `--arc-tolerance` 离散，按包含关系分出层次，再按填充样式取面：`normal`（`hatch_style = 0`）奇偶填充，孤岛成洞，孤岛里的孤岛再填上；`outer`（1）只取最外一层减去第一层孤岛；`ignore`（2）只取最外层。
  - 字段：`layer`、`dxf_type`、`handle`、`pattern`（图案名，实心填充为 `SOLID`）、`solid`、`color`（ACI 色号，256 为随层、0 为随块）、`true_color`（设了真彩色时为 `#rrggbb`）、`hatch_style`、`loops`（边界环数）、`area_m2`、`valid`、`block`、`source_handle`。
  - 边界环不是简单多边形（自相交等）时不填充、不修复：原样保留各个环，`valid = false`，`area_m2` 为空，并给出警告。几个环按填充样式互相抵消、面积为 0 的（例如同一个环画了两遍）也给出警告。
  - 图案和颜色只记录，不渲染。
- **其他实体**：`POINT`、`DIMENSION` 等不提取，按类型计入 `skipped`；没有边界路径的 `HATCH` 记为 `HATCH (no boundary)`。
- **输出**：新目录，含 `raw.gpkg`（`lines`、`closed_polygons`、`texts`、`blocks`、`leaders`、`hatches` 六个图层，无 CRS；没有要素的图层也保留字段结构）、`manifest.json`（`manifest: cad-extract` v1：源文件 SHA-256、文件头、参数、按图层和类型统计的读取数 `read`、各输出图层的要素数、`skipped`、图纸空间数、`z`、`warnings`、输出 SHA-256）和 `record.json`。`read` 的计数口径与 inspect 的图层实体数相同，可以直接对照。写出后回读核对各图层的要素数，不一致不发布。

## 散线围成面

`cad.py polygonize` 把提取出的线按显式容差吸附、打断，再围成面，结果是待审的候选面。缺口和悬挂线只报告，不自动补。

```bash
python cad.py polygonize /work/cad/fatu-raw/raw.gpkg --cad-layers 地块界线,道路红线 --boundary 规划范围线 \
  --snap 0.05 --max-gap 0.5 --min-area 10 --output /work/cad/fatu-faces
```

- **输入**：`cad.py extract` 的 `raw.gpkg`（默认读 `lines` 图层，可用 `--lines-layer` 改），或任何带 `layer` 字段、单位为米的线图层。带 CRS 时必须是以米为单位的投影坐标系，输出沿用它；没有 CRS 时按 extract 的约定视为米。`--cad-layers` 选择参与的 CAD 图层，不给时用全部。
- **吸附（`--snap`，米，必填）**：
  - 距离在 `--snap` 以内的端点合并到它们的平均位置。一条线首尾相距不到 `--snap` 时，因此会被闭合。
  - 合并后仍然孤立的端点，如果离别的线在 `--snap` 以内，就移到那条线上最近的点，并在那条线上插入同一个顶点，处理 T 形接头差一点没碰上的情况。
  - 本来就落在线上的端点只插入顶点，不计入移动。
  - 合并和移动的次数、最大移动距离记入清单。
- **打断与围面**：所有线一起打断（重合的线段去重），再用 GEOS 的 polygonize 围成面。
- **缺口（`--max-gap`，米，必填，须大于 `--snap`）**：打断后只连着一条边的端点视为自由端。自由端之间，或自由端到另一条边，距离不超过 `--max-gap` 的，写入 `qa_gaps`：`kind` 为 `endpoint` 或 `to-line`，另有 `length_m`、两侧句柄 `handles_a` / `handles_b`。同一条边的两端只有在沿边绕行远长于缺口时才算缺口，所以一段短的孤立线不会报成缺口。
- **补缺口**：默认不补。加 `--close-gaps` 才用直线补上全部报告的缺口，补线写入 `added_lines`；用到补线的面 `closed_gap = true`，缺口记录的 `closed = true`。
- **悬挂线**：`qa_dangles` 列出悬挂线（`dangle`）、面内的切割边（`cut-edge`）和无效环（`invalid-ring`），附长度和来源句柄。没闭合的整段线也会出现在这里。
- **面（`faces`）**：字段 `face_id`（按位置从上到下、从左到右编号）、`area_m2`、`status`、`reason`、`valid`、`closed_gap`、`source_handles`（围成这个面的原始线句柄，逗号分隔）、`source_layers`（这些线所在的 CAD 图层）。
  - 面积小于 `--min-area`（m²，必填）的面不删除，`status = hold`，`reason` 写"sliver"。
  - 无效的面同样 hold。其余的面为 `pass`，`pass` 也只是候选。
  - 这一步不判断面的性质，道路面留给文字挂接一步判定。
- **规划范围线（`--boundary`）**：给出范围线所在的 CAD 图层，它的线参与打断（自动加入 `--cad-layers`），并且单独围成范围面；范围外的面不输出，个数记为 `outside_boundary`；清单记录面总面积、范围面积和两者的比值。范围线围不成面时报错。不给时不裁剪，清单注明面积未按规划范围限定。
- **拓扑检查**：对面调用 `topo.py` 的检查（缺口、重叠、碎面、无效几何、重复），碎面阈值取 `--min-area`，结果写入 `qa_topology`。没有 CRS 时，在一个只代表图纸平面的米制坐标系里计算，清单注明。
- **输出**：新目录，含 `faces.gpkg`（`faces`、`qa_gaps`、`qa_dangles`、`qa_topology`、`added_lines` 五个图层；空图层也保留字段结构）、`check.png`（通过的面浅蓝、hold 的面橙色，缺口红色、悬挂线紫色、补线绿色）、`manifest.json`（`manifest: cad-polygonize` v1：参数、用到的线数、吸附统计、各图层要素数、面的通过 / hold / 范围外个数、面积、说明）和 `record.json`。写出后回读核对各图层要素数。

## 填充与面对比

`cad.py compare-fills` 把 `extract` 得到的填充面和围好的面逐个对照，看两种来源的边界是否一致。面可以是 `polygonize` 的 `faces`，也可以是 `label` 或 `xiangyuan` 的 `parcels`。程序只量差异，不吸附、不合并，也不替你选。

```bash
python cad.py extract 法图.dxf --units m --layers C1_T,G1_T,R2_T,DY控制地块 --output /work/cad/fatu-fills
python cad.py compare-fills /work/cad/fatu-xy/parcels.gpkg --faces-layer parcels --fills /work/cad/fatu-fills/raw.gpkg \
  --exclude-hold --output /work/cad/fatu-fill-compare
```

- **输入**：面图层（`--faces-layer`，默认 `faces`）和 `raw.gpkg` 的 `hatches`（`--fills-layer`）。两边的 CRS 必须相同，或者都没有；带 CRS 时须是以米为单位的投影坐标系。`--cad-layers` 只取这些 CAD 图层上的填充；`--exclude-hold` 去掉 `status = hold` 的面，清单记个数。
- **配对**：每个填充配给 IoU（交集面积除以并集面积）最大的那个面。按 IoU 而不是按重叠面积，是因为图框、规划范围这类大面和每个填充都重叠，却和哪个都对不上。妈湾法图里误标成 `DY` 的图框面就是这种情况。
- **图层 `fills`**：填充的全部字段，另加：
  - `match`：`one face`、`several faces`（同时和几个面有重叠，`faces` 记个数）、`no face`、`invalid fill`、`empty fill`；
  - 最佳面的序号 `face_index` 和字段 `face_face_id`、`face_parcel_id`、`face_landuse`、`face_control_type`、`face_status`、`face_layer`、`face_handle`（面图层里有哪些就带哪些）；
  - `overlap_m2`、`iou`；
  - `fill_only_m2`：填充落在所有面以外的面积。
- **图层 `faces`**：每个面的 `fills`（配给它的填充数）、`fills_overlapping`（和它有任何重叠的填充数，包括邻接处的细缝）、`fill_cover_m2`、`face_only_m2`、`valid`。
- **图层 `fill_only`、`face_only`**：两边并集互减后的每一块，带 `area_m2`。贴边的浮点细缝也会列出，面积很小，按 `area_m2` 筛选。
- **无效几何**：无效的填充和面照样列出，但不参与比较，并给出警告。
- **输出**：新目录，含 `fill_compare.gpkg`、`check.png`（只有面的部分为橙色，填充为浅蓝，只有填充的部分为红色，面的边界为灰线）、`manifest.json`（`manifest: cad-fill-compare` v1：输入、`summary`（配对情况、没有填充的面数、IoU 中位数和最小值）、`area`（两边面积、共有面积、`fill_only_m2`、`face_only_m2`））和 `record.json`。
- **妈湾法图实测**（湘源直取地块，`_T` 和 `DY控制地块` 两类填充，去掉 hold 的图框面）：
  - 51 个 `_T` 填充和地块的 IoU 都是 1，边界完全一致；
  - 4 个各 240 m² 的 `_T` 填充不在任何地块里，位于图例区，是图例色块；
  - 07-24 的 G1 填充边界自相交，未参与比较；
  - 01-02、02-04 两块 `G1+C1` 地块没有填充，图里没有 `G1+C1_T` 图层；
  - `DY控制地块` 的 8 个填充一个盖住几个规划控制单元面（IoU 0.13 到 0.26），03-08、03-09、03-10 三个单元面没有填充。

## 文字挂接到面

`cad.py label` 按规则文件把 CAD 文字挂到 polygonize 得到的面上，生成带编号和用地性质的地块。凡是程序不能确定的情况，都列出来交给人工，不替人选择。

```bash
python cad.py label /work/cad/fatu-faces/faces.gpkg --texts /work/cad/fatu-raw/raw.gpkg --rules label-rules.yaml --output /work/cad/fatu-parcels
```

规则文件（YAML）：

```yaml
fields:                       # 一条文字给一个字段：layers 和 / 或 tags（块属性标签），pattern 须整条匹配 text_clean
  parcel_id: {layers: [地块编号], tags: [BH], pattern: '\d{2}-\d{2}(-\d{2})?'}
  landuse:   {layers: [用地代码], pattern: '(R[1-4]|C\d|M\d|GIC\d|S\d)(\+[A-Z]+\d*)*'}
combined:                     # 一条文字拆出几个字段：用命名分组
  - layers: [地块标注]
    pattern: '(?P<parcel_id>\d{2}-\d{2})\s+(?P<landuse>[A-Z]+\d(\+[A-Z]+\d*)*)(\s+(?P<far>\d+(\.\d+)?))?'
landuse_major: {R: 居住, C: 商业服务业, M: 工业, GIC: 公共管理与服务, S: 交通, U: 公用设施, G: 绿地广场}
road: {layers: [道路红线], landuse: [S1]}
```

- **规则校验**：
  - 字段名不能和输出字段重名。
  - 组合规则必须有命名分组。
  - 正则写错、出现未知的段落或键，都直接报错。
  - 有 `landuse_major` 时必须有 `landuse` 字段。
- **文字位置**：用 extract 给的点。设了对齐方式的 TEXT 取对齐点，有单根引线的取箭头点。
- **分类**：每条文字只看覆盖它所在图层（或块属性标签）的规则。
  - 没有规则覆盖的，只按图层计数（`ignored_text_layers`）。
  - 有规则覆盖但一条都匹配不上的，写入 `unparsed_text`。
- **挂接与冲突**：文字点落在哪个面里，就挂到哪个面上。下列情况写入 `label_conflicts`，字段 `kind`、`field`、`value`、`text_raw`、`text_clean`、`layer`、`handle`、`face_id`：
  - `multi-value`：同一个面里，同一字段有两个以上不同的值。该面的这个字段留空，面标为 hold。值相同的重复标注不算冲突。
  - `unassigned`：文字不在任何面里。
  - `on-boundary`：文字点正好落在两个面的公共边上，不挂到任何一个面。
  - `duplicate-id`：两个面得到同一个 `parcel_id`，两个面都标为 hold。
  - `leader-ambiguous`：文字有多个箭头，不挂接。
  - `leader-unlinked`：引线没连到任何已提取的文字，写在箭头处。
- **用地大类**：取 `landuse` 的第一个代码（按 `+` 等分隔），按 `landuse_major` 里最长的前缀匹配，例如 `GIC1` 优先匹配 `GIC` 而不是 `G`。
- **道路面**：满足以下任一条件即为道路面（`road = true`）：
  - 用地代码属于 `road.landuse`。
  - 面上没有任何字段值，而且围成它的线全部来自 `road.layers`。

  道路红线和地块界线画在同一位置时，面的来源图层会包含两者，这时不判为道路面；需要的话，可以在道路面里标上道路用地代码。
- **状态**：不是道路面、又没有 `landuse` 的面，标 hold，原因写 `unknown use`。polygonize 时已经 hold 的面保持 hold，原因合并。
- **输出**：新目录，含：
  - `parcels.gpkg`：
    - `parcels`：`face_id`、`parcel_id`、`landuse`、`landuse_major`、`road`、`status`、`reason`、`area_m2`、`source_handles`、`source_layers`、`label_handles`，以及规则里的其他字段。
    - `label_conflicts`。
    - `unparsed_text`。
  - `check.png`：按用地大类着色，道路灰色，hold 的面描橙边，冲突处标红叉。
  - `rules.yaml`：所用规则的原样副本。
  - `manifest.json`（`manifest: cad-label` v1）：输入的 SHA-256、规则的字段与 SHA-256、各图层要素数、通过 / hold 数、按类型统计的冲突数、`coverage`（面数、道路面数、用途未知数、用途已知的非道路面中有编号的个数和比例）、`ignored_text_layers`。
  - `record.json`。

## 确认后赋 CRS

图纸是城市或项目独立坐标时，不要赋 CRS，改用 [矢量控制点转换](vector-georeferencing.md)，用同名点或官方参数转到国家坐标。

`cad.py assign-crs` 给由这张图纸提取（`cad.py extract`）或转出（`convert.py`）、还没有 CRS 的图层打上已确认的 CRS。只改坐标系标签，不动坐标。`convert.py --crs` 是重投影，不能用来做这件事。

```bash
python cad.py assign-crs /work/cad/fatu-raw/raw.gpkg --layer closed_polygons --inspection /work/cad/fatu-inspect --crs EPSG:4547 \
  --confirmed-by 张三 --basis "与前海范围图层比对重叠，叠加道路中线核对无系统偏移" --output /work/cad/fatu-crs
```

- **确认**：`--confirmed-by`（谁确认的）和 `--basis`（依据什么确认的）必填，不能为空。程序只照实记录，不核实依据本身。
- **前置体检**：`--inspection` 指向这张图纸的 `cad.json`（或其所在目录）。
  - 输入图层的范围必须落在体检时的模型空间范围内；带图纸空间要素、或者坐标被缩放过、平移过的图层会被拒绝。
  - `--crs` 必须在体检时按原坐标（`scale_to_metre` 为 1）列出的候选里。不在时报错，加 `--outside-candidates` 才执行，并在清单里记下 `outside_candidates: true`。
  - 只在按 `$INSUNITS` 换算后才对得上的坐标系（例如毫米图纸），加了 `--outside-candidates` 也拒绝：坐标要先换算成米，这一步还不能代劳。
- **输入**：矢量文件。默认给其中所有图层一起赋 CRS，图层名不变，空图层也照样赋上，例如 `cad.py xiangyuan` 输出的 `parcels.gpkg` 一次就能处理完；只想处理一个图层时用 `--layer`。每个图层的范围都要落在体检记录的范围内；已经有 CRS 的图层直接拒绝，要换坐标系应当做重投影。
- **CRS**：必须是以米为单位的投影坐标系。
- **回读核对**：写出 `<输入名>.gpkg` 后逐层重新读回，逐个比对几何的 WKB 和全部属性值，再核对 CRS；任何一层有一项不一致，整个结果都不发布。输入文件不会被改动。
- **输出**：新目录，含 `<输入名>.gpkg`、`manifest.json`（`manifest: crs-assignment` v1：输入的名称、SHA-256 与图层列表，`crs` 的编号、名称和基准，`confirmed_by`、`basis`、`confirmed_at`，体检报告的路径、SHA-256、对应图纸及其按原坐标列出的候选，`outside_candidates`，`checks`（逐层的要素数、范围和回读核对结果），输出的 SHA-256）和 `record.json`。

## 叠图质检

`cad.py overlay` 把已有 CRS 的地块（`xiangyuan`、`label` 的 `parcels`，或 `polygonize` 的 `faces`）和参考图层（已供地、更新范围、旧规划、行政边界等）叠在一张检查图里，并量出地块和每个参考面的交集。

```bash
python cad.py overlay /work/cad/fatu-crs/parcels.gpkg --exclude-hold \
  --reference /work/data/前海.gdb:国有已供用地 --reference /work/data/更新范围.shp \
  --reference /work/cad/fatu-crs/parcels.gpkg:units --output /work/cad/fatu-overlay
```

- **地块**：`--parcels-layer`（默认 `parcels`）必须是面，必须有以米为单位的投影 CRS；没有 CRS 时报错，先用 `assign-crs` 或[矢量控制点转换](vector-georeferencing.md)。
- **参考**：`--reference PATH[:LAYER]` 可重复；文件只有一个图层时可以不写图层名。参考必须有 CRS，和地块不同时先换算范围、读进来后重投影到地块的 CRS；带 Z 的去掉 Z。只读地块范围向外扩 10%（以长边计）以内的要素。图例名用图层名，没写图层时用文件名，重名加 `#2`。
- **交集**：只算面参考。每一对相交的地块和参考面写进 `overlay.gpkg` 的 `intersections` 层：`parcel_fid`、地块的 `parcel_id` / `landuse` / `control_type` / `status`（有则带上）、`reference`、`reference_fid`、`area_m2`、`parcel_share`（占地块面积的比例）、`reference_share`（占参考面的比例），几何为交集面。面积在地块 CRS 的平面上计算。线、点参考只出图并计数。
- **不修复**：无效的地块和参考面照样出图，在清单里列出 fid，但不参与计算；不做 `make_valid`。
- **hold 与 QA**：`status` 为 `hold` 的地块用橙色粗边标出，不填色。默认计入统计；图框之类被标成 hold 的大面会撑大视图、歪曲比例，这时加 `--exclude-hold`，hold 地块照画、照列，但不参与交集和视图范围。同一文件里名字以 `qa_` 开头的层和 `label_conflicts` 默认画成红色（点为叉）；`--qa-layers a,b` 指定别的层，传空字符串不画。
- **输出**：新目录，含 `overlay.gpkg`、`check.png`、`manifest.json`（`manifest: cad-overlay` v1：输入名称、图层、SHA-256、要素数、参与计算的数量和面积、无效 fid，`crs`，视图范围与外扩比例，逐个参考的路径、图层、读入要素数、几何类型、CRS、是否重投影、是否去掉 Z、无效 fid、碰到的地块数、交集面积（同一参考内按并集计，不重复计）和占参与计算地块面积的比例，hold 的数量、fid 与编号，QA 层要素数，交集条数，输出的 SHA-256）和 `record.json`。
