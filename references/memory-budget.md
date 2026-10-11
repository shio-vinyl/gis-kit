# 内存预算

可选的读前检查：先估算一次整读要占多少内存，超出预算就在读数据之前失败，并给出机读错误。gis-kit 只负责估算和拦截；超限之后改走 spatial-sql、缩小范围还是提高预算，由调用方（如 gis-plugin）决定。

## 设置预算

```bash
"$PY" "$GIS_SKILL/scripts/gis.py" --memory-budget 2GiB terrain /work/dem.tif --params /work/p.json --output /work/out
GIS_KIT_MEMORY_BUDGET=2GiB "$PY" "$GIS_SKILL/scripts/terrain.py" ...
```

- `gis.py --memory-budget SIZE` 把预算以字节数写入子进程的 `GIS_KIT_MEMORY_BUDGET`，不改变当前进程环境；直接调用脚本时读取同一个环境变量。
- `SIZE` 可写字节数，或带单位：`KiB/MiB/GiB/TiB` 按 1024 进位，`KB/MB/GB/TB` 按 1000 进位。非法值由 `gis.py` 以退出码 2 拒绝。
- **未设置预算时不做任何检查**，行为与旧版本相同，也不会为估算而读取样本。
- 预算与 params 里的 `max_pixels`、`max_elements` 等护栏叠加生效，两者都要通过；后者会被 recipe 记录，含义不变。

## 估算方法

| 对象 | 方法 | `basis` |
|---|---|---|
| 栅格 | 宽 × 高 × 同时驻留的网格数组份数 × 工作 dtype 字节数（默认 float64）。份数由各调用点按代码实际持有的数组声明 | `decoded_pixels` |
| 矢量图层 | 抽样前 1000 个要素，算出每要素字节数（属性 deep 内存 + 坐标数 × 16 + 每对象 128 字节），乘以要素数，再乘峰值系数 3 | `sampled_features` |

矢量的峰值系数来自实测：pyogrio 解码时 RSS 峰值是读完后常驻大小的 2.3–3.1 倍（GPKG，2 万–20 万要素，8–512 个顶点）。驱动报不出要素数时不估算、不拦截。估算只用于拦住明显超限的读入，**不是 RSS 上限**；数据分布不均（例如前 1000 个要素特别简单）时会偏低。

## 超出预算

进程在读数据之前退出，**退出码 3**（其他失败仍为 1，参数错误为 2）。已创建的暂存目录照常清理，不发布任何结果。stderr 先输出一行说明，最后一行以 `GIS_KIT_ERROR ` 开头，后接一个 JSON 对象：

```json
{"contract": "error", "schema_version": 1, "code": "memory_budget_exceeded",
 "tool": "terrain", "input": "/work/dem.tif", "layer": null,
 "estimated_bytes": 6400000000, "budget_bytes": 2147483648, "basis": "decoded_pixels"}
```

- `tool`：脚本名；`input`：触发检查的绝对路径；`layer`：矢量图层名，栅格为 `null`。
- 只陈述事实，不给建议。读取方应忽略不认识的字段；不兼容的变更才升 `schema_version`。
- `gis.py capabilities --json` 的 `contracts.memory_budget` 给出 `env`、`exit_code` 和支持的错误版本 `error`，读取方据此判断当前 gis-kit 是否支持这套契约。

## 覆盖范围

目前接入检查的位置：`terrain`、`terrain-detail`、`terrain-backend`、`terrain-cost`，`raster` 的 display / warp / align / mosaic，以及数值栅格的整幅运算和 redistribute 目标网格。按窗口处理的路径内存由窗口大小决定，不检查。矢量工具尚未接入；进度见 GitHub issue #4。

`describe` 卡片里栅格的 `decoded_bytes`（全部波段按各自 dtype 解码后的大小）是一个确定值，可用于事先判断；工作副本（如 float64）会更大。
