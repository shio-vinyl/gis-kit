# Changelog

## 未发布

- `cad.py xiangyuan` 细化：数值字段转成数值（解析不了的整列保留文本并警告），可用 `--schema` 补充字段与数值清单；其他插件的 XDATA 归入 `other_xdata`；同一应用有多个 1000 组码时取最后一个（只统计地块）；`_T` 填充与地块配对核对；配套设施按 `地块编号` 核对是否落在对应地块（含距离）；模型空间有未炸开的 `LZX_LAND` 时报错；报告块定义里的地块属性副本；清单记下单位依据与警告。
- `cad.py assign-crs` 默认给输入文件的全部图层赋 CRS，图层名不变（`--layer` 只处理一个）；每层都核对范围、回读 WKB、属性与 CRS。清单 `input.layers` 为图层列表，`checks` 按图层给出。
- 新脚本 `package.py unpack`：安全解开 ArcGIS 图层包 / 地图包（`.lpk`、`.lpkx`、`.mpk`、`.mpkx`；7z 经 bsdtar 或 7-Zip，zip 经 zipfile）。解压前拒绝越界路径、链接和超过 `--max-bytes` 的包，解压后核对大小。读出包信息，为 `commondata/` 下每个数据集写 describe 卡片，把图层文件对应到数据：`.lyrx` 读渲染字段、分类、标注与定义查询，`.lyr` 只取数据源路径、名称与标注表达式；包里缺数据的图层单独报告。符号化不还原。说明见 `references/esri-package.md`。
- `join.py --on 左字段=右字段`：按属性挂接，右侧可以是 CSV / Excel 表（`--right-sheet`、`--right-header-row`、`--right-encoding`；单元格按文本读入，前导零不丢，全数字的列转为数值）。`--normalize id` 按地块编号规范化（全角转半角、连字符统一、去空格、去每段数字的前导零）。报告列出两侧对不上的编号、空编号、重复编号、规范化后撞到一起的编号，以及每一处规范化的对照。原有的空间挂接行为不变。
- 新子命令 `cad.py xiangyuan`：湘源控规图则直取。带 `用地代码` 扩展数据的闭合多段线即地块，扩展数据（每个属性一个应用名）按原名全部转成字段，另加 `parcel_id`、`landuse`、`landuse_name`、`control_type`、`unit_id`、计算面积与 `用地面积` 的差。图层与用地代码不符、删除标记、缺编号、重复编号、无效几何、面积差超限的面标 hold；编号图块逐个核对；`DY范围` 按 `DY编号` 命名为单元（重复的面识别出来）；另取配套设施与规划边界。`--units header` 与 `用地面积` 矛盾时拒绝。妈湾法图实测：54 个地块、45 个规划控制单元、97 个编号图块全部一致。
- 新子命令 `cad.py convert`：调用本机已装的 ODA File Converter 或 LibreDWG（`dwg2dxf`），在副本上把 DWG 转成 DXF，回读版本、实体数、图层数与范围，写入 `manifest.json`（`cad-convert` v1）；不安装转换器，没有时报错。用妈湾法图（AC1032）实测 LibreDWG 0.14 可转。
- `cad.py inspect` / `extract` 的编码处理：R2007 及以上的 DXF 一律按 UTF-8 读（此前 GDAL 按 `$DWGCODEPAGE` 读，ANSI_936 的 R2018 图纸会乱码），早期版本疑似 GBK 被读成 Latin-1 时自动用 CP936、GB18030 重读，取无法解码最少的一种，并记入 `encoding`。GDAL 读取取消每块 10000 要素的上限，多面网格不再导致读取失败；LibreDWG 写出的不合规 XDATA 会在副本中去掉后再交给 ezdxf，并计数。
- 新脚本 `vector-georef.py`：用经过检查的四参数（默认）或六参数仿射变换，把局部平面坐标的矢量（城市独立坐标、CAD 成果）转到投影坐标系。`fit` 由显式标注 fit/check 的同名点拟合，按事先给定的检查点 RMSE / 最大误差判定 pass 或 hold，并画残差图；`define` 记录官方或用户给的参数，可用点检查；`apply` 转换全部图层，hold 的变换需 `--allow-hold`，落在拟合点凸包外的要素标为 `extrapolated`。说明见 `references/vector-georeferencing.md`。
- 新子命令 `cad.py label`：按 YAML 规则（单字段、命名分组拆多字段、块属性标签、用地大类最长前缀、道路面规则）把 CAD 文字挂到 polygonize 的面上，输出 `parcels.gpkg`（`parcels`、`label_conflicts`、`unparsed_text`）、`check.png`、规则副本与 `manifest.json`（`cad-label` v1）。多值、面外、公共边上、重复编号、多箭头和未连接引线都列为冲突，不替人选择；不是道路又无用途的面标 hold。
- `cad.py extract` 读取引线：被 LEADER 标注的文字放到箭头处，MULTILEADER 的文字写入 `texts`，箭头写入新图层 `leaders`；`texts` 新增 `arrows`、`leader_handle`。`cad.py polygonize` 的面新增 `source_layers`。
- 新子命令 `cad.py polygonize`：把 `cad.py extract` 的线按显式容差围成候选面。`--snap` 内的端点合并、孤立端点移到线上（插入共享顶点）；打断后用 polygonize 围面。`--max-gap` 内的缺口写入 `qa_gaps`，只有加 `--close-gaps` 才补线并标记；悬挂线、切割边写入 `qa_dangles`；小于 `--min-area` 的面与无效面标 hold、不删除；`--boundary` 按规划范围线裁剪并记录面积比；面另经 `topo.py` 检查。输出 `faces.gpkg`、`check.png`、`manifest.json`（`cad-polygonize` v1）。
- `convert.py` 目录批处理新增识别 `.kmz`、`.gpx`、`.gml`、`.tab`、`.mif`、`.dxf`、`.fgb`、`.geojsonl`（驱动已在 Pyogrio 自带的 GDAL 中，不新增依赖）。行为变化：多图层输入未给 `--layer` 时报错并列出图层，不再静默读取第一层；多个输入映射到同名输出时整批在写出前拒绝。删除未被调用的 `infer_driver`。
- 新脚本 `cad.py inspect`：清点 DXF 的图层、实体类型、文字样例、图纸单位、离群实体与图纸空间，按坐标量级与 EPSG 适用范围列出候选 CRS（带带号的中国高斯-克吕格 3/6 度带与经纬度；四种基准并列；`--near` 剔除过远的候选）。无带号坐标与城市独立坐标分不开，一律 `hold`、不列 EPSG；不赋 CRS。`--encoding` 处理缺代码页的中文图纸。输出 `cad.json`、`check.png`、`record.json`；DWG 拒绝并提示先转 DXF。说明见 `references/cad-intake.md`。
- `cad.py inspect` 接入可选依赖 ezdxf（`requirements-full.txt`、`environment.yml`；测试版本 1.4.5）：图层清点按 CAD 口径计数，给出 DXF 实体类型、闭合与不闭合多段线、块名与块属性、原文与清洗后的文字、图层关闭 / 冻结状态、未使用图层、布局与视口；几何、范围与 CRS 仍来自 GDAL。未安装时退回 GDAL，并在 `reader.missing` 列出缺少的能力。`capabilities` 新增 `cad` 一项。
- 新子命令 `cad.py extract`（需要 ezdxf）：把选定图层的模型空间实体写成 `raw.gpkg` 的 `lines`、`closed_polygons`、`texts`、`blocks` 四个图层，每个要素带 `layer` 与 `handle`；曲线与凸度按 `--arc-tolerance`（弦高，米）离散；`--units m|mm|header` 必填，`header` 在无单位或与坐标矛盾时拒绝；非零 Z 需 `--drop-z`；文字含原文与清洗文字、对齐点与块属性；`--include-blocks` 炸开图块（0 层随引用图层）。不设 CRS，清单为 `manifest.json`（`cad-extract` v1），读取计数与 inspect 一致。
- 新子命令 `cad.py assign-crs`：给由已体检图纸转出、无 CRS 的图层打上已确认的 CRS，只改标签不动坐标。`--confirmed-by` 与 `--basis` 必填；CRS 须为体检按原坐标列出的候选（否则需 `--outside-candidates` 并记入清单），只在单位换算后才符合的坐标系一律拒绝；输入范围须在体检的模型空间范围内。回读逐个比对几何 WKB、属性与 CRS 后才发布，清单为 `manifest.json`（`crs-assignment` v1）。
- `cad.py inspect --reference`：与 CRS 已知的参考图层比对范围，判为范围重叠、带号差异、疑似平移或旋转、量级不同四种；前两种把参考 CRS（或换算带号后的同基准坐标系）列为候选，不带带号的坐标由此得到 EPSG。参考图层须为以米为单位的投影坐标系。
- `buffer.py --distances 400,800,1200`：同心圈，每个要素按距离各出一个圆面，带 `buffer_m` 与 `ring`（1 为最内圈）；`--dissolve` 时逐圈合并。供 Composer 的 `rings` 编码块画缓冲圈 / 服务范围图（Composer #8）。
- 修复：原子写出的 GeoJSON 把随机临时文件名（`.<名>.<随机>.tmp`）写成了图层名，同样的输入每次产出不同字节；现在图层名取输出文件名，重复运行逐字节一致。
- 新脚本 `display-vector.py`：按一张图的视角（中心、缩放、画布，Web 墨卡托 512 像素瓦片，与 Composer `inspect` 一致）概括面数据：删去几平方像素以下的部件、按半像素共边简化、陆上国界只取相邻单元的公共边、海岸取合并后的外轮廓；`--keep` 保留必须出现的小单元，`--cache` 按源标识、参数与视角缓存。发布 `areas.geojson`、`boundaries.geojson`、`coast.geojson` 与清单 `display-vector` v1，`capabilities` 声明 `contracts.display-vector`（Composer #30）。
- `admin-chain.md` 的 Composer 用法改为地图清单与分面（原 sheet 组合已在 Composer 中移除）。
- 新脚本 `admin-chain.py`：按场地点或场地面，在 GADM 式分级 GeoPackage 里逐级找出包含场地的行政区，并取每级下一级的单元作内部界线；按各级范围做共边显示简化，发布 `chain.geojson`、`context.geojson` 与清单 `admin-chain` v1，供 Composer 的 sheet 组合做区位逐级放大图。国界口径用 `--adm0-layer` 显式选择。`capabilities` 声明 `contracts.admin-chain`（Composer #8）。
- `terrain.py` 新增可选 `hillshade`：由 Horn 梯度算出 0–1 朗伯光照（`hillshade.tif`，单位 ratio），可设光源方位、高度角与 `z_factor`；只出数值，交给 Composer 时以显示栅格写出、由主题上色（Composer #13）。
- 修复：受限 macOS 沙箱里 `system_profiler` 返回的字体 plist 缺少 `_items`，matplotlib 3.9 建字体缓存时抛 `KeyError`，且因缓存从未写成而每次复现。`_sandbox` 在缓存缺失时先跳过 `system_profiler` 预建一次（仍扫描 fontconfig 与系统字体目录）。
- 新脚本 `snapshot.py`：从白名单服务商（`references/snapshot-providers.json`）按范围与缩放级别把 XYZ 瓦片快照到项目 `inputs/`，清单 `tile-snapshot` v1 记录 URL、获取时间、许可、署名与逐瓦片 SHA-256；条款禁止缓存的服务直接拒绝。`capabilities` 声明 `contracts.tile-snapshot`（Composer #13）。

- `gpkg.py rename` 改为用 sqlite3 在单个事务内原地重命名图层：同步更新 GPKG 元数据表、R-tree 空间索引及其触发器，不再整层读写，FID 与字段类型保持不变，也不再残留 `rtree_<旧名>_*` 孤表（#4）。
- `gis.py describe <path> --json`：输出只读元数据卡片，不读要素或像元；格式见 `references/describe.schema.json`。
- `gis.py capabilities --json`：输出版本、读写格式与可导入性探测，供外部诊断工具读取，不导入后端。
- `gis.py --memory-budget SIZE`（或环境变量 `GIS_KIT_MEMORY_BUDGET`）：可选的读前内存预算，超限时以退出码 3 失败，并在 stderr 输出机读错误；未设置时行为不变。地形与栅格整幅运算已接入，契约见 `references/memory-budget.md`。`describe` 栅格卡片新增 `decoded_bytes`，`capabilities` 新增 `contracts.memory-budget`。
- 矢量工具接入内存预算：读入整个图层的公开工具在读之前按抽样估算检查；`convert`（输出 GPKG）与 `gpkg import` / `copy` / `export`（输出 GPKG 或 Shapefile）对超过 10 万要素的图层改为分块读写，字段类型与几何类型与一次性写出一致。`hydro-review` 与栅格整理操作也接入了栅格预算检查。
- 写出后的回读校验改为分块或按窗口读取，峰值内存不再因整份回读翻倍；校验口径与 `decoded_sha256` 等记录值不变。
- Composer 交接改为共用 Composer 项目工作区：gis-kit 把分析输入原子发布到项目的 `inputs/<分析名>/`，Composer 用 `--project` 渲染到 `runs/<run-id>/`，两边的 SHA-256 记录可以逐个核对溯源。`tests/run_composer_acceptance.py` 改为这一流程，搬移后用 `rerun` 逐字节复核，失败用例按错误代码断言；约定写在 `references/composer-display.md`（#9）。
- 新增两份交给地图渲染器的约定：`class-scheme` v1（分类方案，只记语义不带颜色）和 `display-raster` v1（不上色的显示栅格），Schema 在 `references/`，`capabilities` 的 `contracts` 声明写出版本。`raster.py display` 新增 `"mode":"values"`：类别序号编码（`class-index`）或 24 位数值编码（`value-rgb24`），同时写出方案和 `raster.json`；原有上色模式保留，供独立使用和检查图。新脚本 `scheme.py` 为矢量字段生成分类方案，支持手动断点、分位数、等间距和类别列表，记录每类计数与 NoData 计数。Composer 验收改为同一份方案驱动矢量与栅格，并用三套主题核对颜色（#9）。

## v0.1.0 — 2026-09-30

首个公开版本。

- 矢量、栅格、地形、路网覆盖、扫描地图描绘与配准、Spatial SQL、recipe / trace 等现有能力整体公开。
- 采用 Apache-2.0 许可证。
- 依赖分为 core / full / tested / spatial-sql 四层，另提供 conda-forge `environment.yml`。
- 中文字体改为按 `config/user.yaml` 的候选顺序选择，默认 Noto Sans CJK SC。
- 测试不再假定仓库目录名；真实数据验收的外部输入改为命令行参数。
