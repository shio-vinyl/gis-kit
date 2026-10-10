# Changelog

## 未发布

- `gpkg.py rename` 改为用 sqlite3 在单个事务内原地重命名图层：同步更新 GPKG 元数据表、R-tree 空间索引及其触发器，不再整层读写，FID 与字段类型保持不变，也不再残留 `rtree_<旧名>_*` 孤表（#4）。
- `gis.py describe <path> --json`：输出只读元数据卡片，不读要素或像元；格式见 `references/describe.schema.json`。
- `gis.py capabilities --json`：输出版本、读写格式与可导入性探测，供外部诊断工具读取，不导入后端。

## v0.1.0 — 2026-09-30

首个公开版本。

- 矢量、栅格、地形、路网覆盖、扫描地图描绘与配准、Spatial SQL、recipe / trace 等现有能力整体公开。
- 采用 Apache-2.0 许可证。
- 依赖分为 core / full / tested / spatial-sql 四层，另提供 conda-forge `environment.yml`。
- 中文字体改为按 `config/user.yaml` 的候选顺序选择，默认 Noto Sans CJK SC。
- 测试不再假定仓库目录名；真实数据验收的外部输入改为命令行参数。
