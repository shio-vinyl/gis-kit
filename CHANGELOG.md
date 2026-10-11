# Changelog

## 未发布

- `gpkg.py rename` 改为用 sqlite3 在单个事务内原地重命名图层：同步更新 GPKG 元数据表、R-tree 空间索引及其触发器，不再整层读写，FID 与字段类型保持不变，也不再残留 `rtree_<旧名>_*` 孤表（#4）。
- `gis.py describe <path> --json`：输出只读元数据卡片，不读要素或像元；格式见 `references/describe.schema.json`。
- `gis.py capabilities --json`：输出版本、读写格式与可导入性探测，供外部诊断工具读取，不导入后端。
- `gis.py --memory-budget SIZE`（或环境变量 `GIS_KIT_MEMORY_BUDGET`）：可选的读前内存预算，超限时以退出码 3 失败，并在 stderr 输出机读错误；未设置时行为不变。地形与栅格整幅运算已接入，契约见 `references/memory-budget.md`。`describe` 栅格卡片新增 `decoded_bytes`，`capabilities` 新增 `contracts.memory_budget`。
- 矢量工具接入内存预算：读入整个图层的公开工具在读之前按抽样估算检查；`convert`（输出 GPKG）与 `gpkg import` / `copy` / `export`（输出 GPKG 或 Shapefile）对超过 10 万要素的图层改为分块读写，字段类型与几何类型与一次性写出一致。`hydro-review` 与栅格整理操作也接入了栅格预算检查。
- 写出后的回读校验改为分块或按窗口读取，峰值内存不再因整份回读翻倍；校验口径与 `decoded_sha256` 等记录值不变。

## v0.1.0 — 2026-09-30

首个公开版本。

- 矢量、栅格、地形、路网覆盖、扫描地图描绘与配准、Spatial SQL、recipe / trace 等现有能力整体公开。
- 采用 Apache-2.0 许可证。
- 依赖分为 core / full / tested / spatial-sql 四层，另提供 conda-forge `environment.yml`。
- 中文字体改为按 `config/user.yaml` 的候选顺序选择，默认 Noto Sans CJK SC。
- 测试不再假定仓库目录名；真实数据验收的外部输入改为命令行参数。
