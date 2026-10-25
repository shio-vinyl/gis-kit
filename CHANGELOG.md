# Changelog

## 未发布

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
