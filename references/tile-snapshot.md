# 在线瓦片的本地快照

`snapshot.py` 把白名单内服务商的 XYZ 瓦片按范围和缩放级别下载到一个新的结果目录，供 Composer 等渲染器离线读取。渲染器只读快照，不在渲染时联网，所以重渲染可以逐字节复核。

```bash
python snapshot.py --params snapshot.json --output /absolute/project/inputs/site-imagery --dry-run   # 只报瓦片数
python snapshot.py --params snapshot.json --output /absolute/project/inputs/site-imagery
```

```json
{"schema_version":1,"provider":"eox-s2cloudless-2016","bounds":[121.45,31.18,121.53,31.25],"minzoom":12,"maxzoom":15,"max_tiles":2000}
```

- **白名单**：`references/snapshot-providers.json`。每个条目写明 URL 模板、类型（`raster` / `raster-dem`）、格式、许可、署名、条款链接与 `cache`。条款不允许缓存的服务（OSM 官方瓦片、Esri 影像、Google）列为 `forbidden`，请求时直接失败并给出原因，不会下载任何瓦片。新增服务商先核对条款，再写进白名单。
- **规模**：默认 `max_tiles` 2000，超出即失败；先用 `--dry-run` 看瓦片数。请求之间默认间隔 0.05 秒（`delay_seconds`），失败重试 3 次；404 等硬错误立即失败，整个结果目录不发布。
- **输出**：`tiles/{z}/{x}/{y}.{ext}`、`snapshot.json`（契约 `tile-snapshot` v1）与 `record.json`。清单记录服务商信息、`retrieved_at`（UTC）、范围、缩放级别、瓦片尺寸、每个瓦片的路径与 SHA-256，以及按路径排序后的 `tiles_sha256` 汇总。瓦片按服务原样保存，不重新压缩、不重投影。
- **署名与许可**：清单中的许可和署名取自获取时的白名单条目，Composer 会把署名自动写进来源行。转载或正式发布前仍需核对服务商条款；`cache: allowed` 只表示条款允许为离线渲染保存瓦片。
- **不在范围**：矢量瓦片（PMTiles 裁切）与 WMS/WCS 尚未支持；大体量矢量切片见 Composer #4。

Composer 侧以 `{"type":"tile-snapshot","path":"../inputs/site-imagery/snapshot.json"}` 引用，读取前逐个核对瓦片哈希。
