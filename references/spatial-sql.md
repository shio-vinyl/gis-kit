# Spatial SQL 与规模路径

大表筛选、空间连接、分组汇总先考虑 DuckDB Spatial / Parquet 的原生 SQL，避免先用 `GeoPandas.read_file/read_parquet` 全量载入。小数据、复杂 Python 算法继续用现有 GIS stack；已有 `join.py` / `daily.py` 不会自动切换引擎，旧返回语义保持不变。

## 选择与边界

- **DuckDB Spatial：** 让引擎选择 join、列裁剪、过滤下推与 spill；先看 `EXPLAIN` 的实际 `SPATIAL_JOIN`，再测对应负载。不要用循环分块模拟全局 join / dissolve，不增加 Python multiprocessing。空间 join 的 build side、GEOS 分配及某些聚合仍可能占较多内存；`memory_limit` 是引擎预算，**不等于 RSS 上限**。失败应缩小任务、减少列/线程或选择专业后端，不静默切回全量 GeoPandas。
- **GDAL/OGR、GeoPackage：** 单层转换、裁剪和受支持的 driver SQL 直接走成熟工具；DuckDB 原生 `ST_Read` / GDAL `COPY` 可做单机 I/O。GPKG 是含空间元数据/索引的 SQLite 容器，普通 SQLite 本身不提供完整空间函数。多表空间汇总优先实测 DuckDB；需要事务、多用户或已有服务时按需用 PostGIS，不建设服务管理层。
- **GeoParquet：** 适合列式扫描与中间数据复用，但无需转换所有数据。一次轻量 OGR 筛选能完成时不要先转 Parquet。native SQL、CLI 或 Python API 已足够的任务直接调用；下述 adapter 只为需要新目录发布、回读与拒绝条件的 Parquet 结果提供薄边界。

## 已测运行时

可选依赖是 **DuckDB 1.5.0 + 官方 spatial 扩展 8734819**，同时复用 PyArrow / PyProj。DuckDB 1.5 引入带 CRS 的 GEOMETRY 类型，此处不能沿用旧版本只用无 CRS WKB 的假设。独立可选依赖见 `requirements-spatial-sql.txt`；不会放入每次普通 GIS 调用的必装路径。

安装需授权，使用已经选定的 `$PY`：

```bash
"$PY" -m pip install -r "$GIS_SKILL/requirements-spatial-sql.txt"
"$PY" -c 'import duckdb; c=duckdb.connect(); c.execute("INSTALL spatial; LOAD spatial"); print(c.execute("SELECT version()").fetchone())'
```

`INSTALL` 下载版本/平台对应的官方二进制；扩展位于该用户的 DuckDB 扩展目录，不随 skill 复制。实际执行只 `LOAD`，关闭隐式安装和自动加载，缺依赖直接失败。新版本/平台须重跑验收。

## 极薄入口：spatial-sql.py

SQL 文件中写**一条原生 SELECT / WITH**，包含绝对本地路径；没有自定义 SQL 语法、表达式 wrapper 或 SQL 生成器。例如两个已核实为相同 CRS 的输入：

```sql
SELECT z.zone_id AS id, count(*)::BIGINT AS n, first(z.geom) AS geom
FROM read_parquet('/work/points.parquet') p
JOIN read_parquet('/work/zones.parquet') z ON ST_Intersects(p.geom, z.geom)
WHERE p.observation_year = 2025
GROUP BY z.zone_id;
```

```bash
"$PY" "$GIS_SKILL/scripts/gis.py" --trace /work/trace spatial-sql /work/query.sql \
  --input /work/points.parquet --input /work/zones.parquet \
  --id id --geometry geom --crs OGC:CRS84 --geometry-types POLYGON MULTIPOLYGON \
  --memory-limit 512MB --threads 2 --max-temp-size 10GB --output /work/result
```

- `--input` 可重复，只接收明确的本地单文件 Parquet / GeoParquet。SQL 仍用原生文件函数，不注册自定义表名。拒绝通配分区、远端、数据库附件及多文件来源；需要这些能力时由 Agent 直接使用原生引擎并承担相应文件验证。本入口的文件 allowlist 不适配 GDAL 的 sibling-directory 探查，因此 GPKG 不直接进入此封装；实测 ST_Read 原生路径可用，按需在封装外转换一次即可。
- `--id` 要求输出字符串/整数 ID 唯一且非空；拆分、join 重复或汇总后的 ID 由 SQL 作者明确设计，程序不自动重编号。几何输出必须明确唯一 `--geometry`、预期 `--crs` 和允许类型；纯属性聚合省略这三项。多个几何列不支持。默认拒绝 null / empty / invalid 几何，显式 `--allow-null/empty/invalid` 可保留并计数，**不会自动修复**。
- `--schema /work/schema.json` 可断言完整的名称→DuckDB 类型，例如 `{"id":"INTEGER","n":"BIGINT","geom":"GEOMETRY('OGC:CRS84')"}`。始终逐列比较 SQL 输出类型与持久化回读类型，不做宽松类型兼容。`sum(BIGINT)` 等可能得到 HUGEINT，Parquet 回读为 DECIMAL；需要整数时在 SQL 中显式 cast，并承担溢出检查。
- `--output` 必须是新目录，复用 `_delivery.bundle/fingerprint`。单次原生 COPY 直接写最终 Parquet，不生成全量 Python DataFrame 或中间明细 join 文件；仅元数据、计数/范围/类型等小型聚合进入 Python。回读、ID/几何/CRS/schema 验证及输入/SQL/断言文件前后 SHA-256 通过后才发布。spill 在包内暂存、正常失败/成功均清理；强制杀进程后的残留遵循现有 delivery 边界，不保证耐掉电。

成果含 `result.parquet`、原 SQL、`plan.txt`、`record.json`，记录引擎版本、预算、耗时、schema、CRS/轴单位、XY bbox、几何计数和文件指纹。`complete` 仅表示执行和声明的边界检查通过，不代表 candidate/unknown 被批准，也不证明任意 SQL 的业务含义。原输入不写入；哈希检测变化后拒绝发布，不提供对其他进程并发修改的回滚。

复用 `gis.py` 发现、调用和可选 trace。由于任意 SQL 的依赖、非确定函数和字段语义无法由固定 recipe adapter 推定，trace 仅记录调用级元数据，`recipe_eligible:false`。本轮刻意不接入 recipe 缓存、不扩展 semantic-check 的全量属性读取；规模语义约束由 Agent 用显式 SQL 比较未知值计数/稳定 ID 或有界独立回算。SQL 文件会复制到结果包，含路径与业务逻辑，分享前审阅；可信本地 SQL 是前提，单 SELECT 与文件 allowlist **不构成恶意 SQL 安全沙箱**。

## CRS 与计量必须由任务明确

入口只检查输出 CRS，不推断输入坐标的真值、不自动重投影、不为未知几何填 CRS。`ST_SetCRS` / `::GEOMETRY('...')` 只标记；`ST_Transform` 才重投影。经纬度明确 XY 顺序；转换时核查 `always_xy` 与实际坐标。不同 CRS 的原生二元运算可拒绝，但显式去除 CRS 后引擎无法保护语义。

**实测两个 PROJ 环境的 NAD83/WGS84 转换结果不同。** DuckDB 与 PyProj 将同一纽约坐标转到 EPSG:2263 时相差约 0.40 / -3.01 US survey foot，导致边界点分区计数不一致。规模验收因此将小型区划层用明确的 PyProj 路径一次转换到 OGC:CRS84，两种 join 引擎读取同一份规范化边界，点保留原始经纬度；不把不同坐标操作的结果差异当成 join 误差，也不承诺跨环境重投影逐点一致。需要这种一致性时固定并独立验证坐标操作与网格。

`ST_Area` / 距离的单位与 CRS、函数定义有关；EPSG:2263 是 US survey foot，不能把平方坐标单位标成平方米；地理 CRS 的平面运算也不能标成米。方法、适用投影、测地模型、单位换算由 Agent 明确 SQL 或选择现有计量工具。record 的轴单位仅供审查，程序不解析 SQL 来证明计量正确。

已测 DuckDB 1.5.0 对 **零行或全 null 的几何列**可能丢失 GeoParquet 元数据；无 CRS 的 GEOMETRY 输出也可能回读为 CRS84。adapter 对这些类型/CRS 变化失败关闭，绝不补标签冒充保真；无几何的零行表可正常交付。geometry-types 断言基础 OGC 类型，Z/M 等详细类型记录于 GeoParquet 元数据；不证明拓扑、属性语义、行顺序或稳定 ID 的跨数据版本身份。

## 验证与复跑

普通回归 `tests/test_spatial_sql.py` 使用小夹具和真实 CLI，不安装扩展、不下载数据。规模验收独立运行：

```bash
PYTHONDONTWRITEBYTECODE=1 "$PY" "$GIS_SKILL/tests/run_spatial_sql_acceptance.py" \
  --csv /work/green-coordinates.csv --zones /work/zones.gpkg \
  --sizes 100000 500000 1500000 --repeats 3 --output /work/new-scale-run
```

CSV 使用 NYC Open Data `gi8d-wdg5` 的 `:id,pickup_longitude,pickup_latitude,passenger_count`，按 `:id` 排序下载最多 150 万条；区划采用 TLC taxi zones，GPKG 保留 OBJECTID 与 EPSG:2263。脚本不自动联网。保留真实源文件 SHA-256，明确剔除纽约范围外/空坐标数量；以 PyArrow 有界批次读取原始 CSV + Shapely STRtree 独立回算全部计数和乘客和。两条执行路径同谓词/同规范化边界，记录每个新进程 wall time 与 lifetime peak RSS；SQL 进程禁止构造 pandas DataFrame。性能数字、版本与验收边界在仓库开发计划中记录，不放实验流水账到 skill。

技术依据：[DuckDB Spatial joins](https://duckdb.org/2025/08/08/spatial-joins)、[GEOMETRY / CRS](https://duckdb.org/docs/current/sql/data_types/geometry)、[GDAL I/O](https://duckdb.org/docs/current/core_extensions/spatial/gdal)、[TLC 数据说明](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)。官方说明是能力依据，具体版本的正确性与性能以实测为准。
