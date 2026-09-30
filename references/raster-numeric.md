# 栅格数值变换、局部更新与总量守恒

入口继续使用 `raster.py operation input.tif [other.tif ...] --params params.json --output /absolute/new-bundle`。旧裁剪、对齐、拼接、按值重分类和单阈值计算见 [基础栅格](analysis-extensions.md)。参数中的文件路径按当前工作目录解析；输出必须为新目录。先确定输入是每像元总量、每平方米密度、比率或类别，再选择操作。

## 公共契约

新操作 `transform/combine/update/rasterize/bands/allocate/redistribute` 复用原入口的输入指纹、网格检查、暂存及整包发布。`record.json` 保存参数、源波段元数据、输入/实现 SHA、派生产物与实际解码验证；外部查表和排除区也参与指纹。原图不变，不把派生权重当作观测证据。

- 要求已知 CRS、北向上网格；多输入必须同 CRS、原点、像元大小、宽高，禁止隐式对齐。先用 `align` 或 `warp` 明确重采样。外部 raster sidecar 和活动 SQLite sidecar 拒绝。`rasterize` 的输入用作参考网格，其他数值操作读取选定波段；`redistribute` 的目标另用 `reference`。
- `operands: [{"input":0,"band":2},{"input":1,"band":1}]` 明确操作数次序；输入索引从 0 起，波段从 1 起。缺省为每个输入的 `band`（默认 1），不会隐式选取全部波段。无效值及掩膜转为缺失；有效零保持有效。未经校准的 scale/offset 非默认波段拒绝分析。
- `combine`、`bands`、`update` 及局部 `transform`（rules/clip/invalidate/log/exp/sqrt/abs/power）默认逐窗口读取、计算、编码、写出和回读。`block_size` 默认 256；`max_elements` 默认 10000000，在这些路径限制 **操作数数量 × 单窗口像元数**，临时数组仍为其数倍。`gdal_cache_mb` 默认 64，限制 GDAL 块缓存；GTiff 使用 256×256 存储块，COG 转换使用单线程。计算窗口与存储块无需相同。`record.execution` 记录实际路径、窗口与缓存限制。
- `transform` 的 auto_class/minmax/zscore、`rasterize`、`allocate/redistribute` 继续使用有界全量数组，`max_elements` 在这些路径限制全图操作数或贡献波段元素数；全局分位数、归一化和精确守恒没有被改成分块近似。几何精确求交另有 `max_intersections`，默认 2000000，超限失败。整理模块的连通标记等也不属于局部窗口链。
- 两类路径均保留 `max_pixels` 默认 50000000 的单输入总像元保护；窗口化不自动取消任务规模上限。窗口路径不保留全图 NumPy 数组或逐窗口哈希列表；解码哈希亦分段计算。但 GDAL 源压缩块、驱动元数据、矢量掩膜几何和分类规则是额外成本，`gdal_cache_mb` 不等于进程 RSS 上限。处理巨大 strip、复杂矢量或大量波段前需核查输入结构并实测；不承诺所有算子、输入编码或 Composer 显示路径均低内存。
- 新操作输出默认 Float64 / NaN，可选 `dtype: uint8/int16/uint16/int32/uint32/float32/float64`、`format: GTiff/COG`。整数需 `nodata_value`，默认拒绝小数；显式 `rounding: nearest_even/floor/ceil` 后检查范围及有效值与 NoData 冲突。Float32 记录最大编码误差，浮点只用 NaN NoData。`allocate/redistribute` 固定 Float64；整数守恒使用专门余量分配，不使用普通类型截断。每个产物所有像元均解码并验证掩膜、网格、类型后才发布；窗口路径通过增量摘要检查写入值与回读值，不重新生成全图数组。新操作的 decoded SHA 按波段行优先，与回读块大小无关；旧栅格哈希仍带块大小。

## 局部计算链

波段指数使用现有文件组合：先 `bands` 明确选择并按需校准，再用 `combine/subtract` 得到分子、`combine/add` 得到分母，最后 `combine/divide`。例如同量纲 A/B 的 `(A-B)/(A+B)`；`invalid: nodata` 显式将零分母置缺失，默认仍报错。未选中分支缺失不污染 `where` 的结果。不同格网先独立 `align`，不在计算中偷偷重采样。

```json
{
  "method": "subtract",
  "operands": [{"input": 0, "band": 2}, {"input": 0, "band": 1}],
  "block_size": 256,
  "max_elements": 10000000,
  "gdal_cache_mb": 64,
  "dtype": "float64",
  "format": "COG"
}
```

参数文件配合 `raster.py combine /data/calibrated.tif --params /data/difference.json --output /data/new-difference`。每步独立整包发布；后一步失败不删除前一步已验证的结果，但失败步不发布结果目录。已有 `zonal` 可直接消费最终 GeoTIFF/COG 并生成 `record.json` 区域表，无需新建统计协议。窗口大小只影响计算分块；比较不同块大小使用数值、掩膜和 `decoded_sha256`，不要求压缩文件字节相同。

## 赋值、分档与计算

`reclassify` 扩展原有 `mapping`，也接受 `rules`、CSV `table`。三类规则按 **rules → mapping → table** 顺序执行。`unmapped: keep/nodata/error`，默认 nodata；缺失输入不参与规则。`overlap: error/first/last`，默认 error：有效像元同时命中多条规则时报错，first/last 按规则次序决定。输出仍为原有单波段 Float64；需要类型控制可接 `bands`，或用 `transform` 的 rules 方法。

```json
{
  "rules": [
    {"min": 0, "max": 10, "output": 1},
    {"min": 10, "max": 20, "closed": "both", "output": 2},
    {"value": 99, "output": null}
  ],
  "unmapped": "keep",
  "overlap": "error"
}
```

区间 `closed: left/right/both/neither`，默认 `[min,max)`；端点有限，反向区间拒绝。`output: null` 显式剔除。CSV 使用 UTF-8，表头为 `value,output` 或 `min,max,output`，数值均须有限；CSV 区间固定左闭右开，其他端点策略使用 JSON rules。

| 操作 | 参数及边界 |
|---|---|
| `transform` / `method: rules` | 与重分类相同，支持新操作的输出类型控制 |
| `transform` / `auto_class` | `mode: equal/quantile`、正整数 `classes`；对全栅格有效值统计，保存 breaks。内部区间左闭右开，最大值归末类；重复分位点折叠，常量只有 1 类；全缺失报错 |
| `transform` / `minmax/zscore` | 分别输出 0—1 或总体标准差标准分。常量默认报错，显式 `constant: zero/nodata` 决定结果；全缺失报错 |
| `transform` / `log/exp/sqrt/abs/power` | power 需 `exponent`；`invalid: error/nodata` 控制定域错误及溢出，默认 error |
| `transform` / `clip/invalidate` | `min/max` 两端包含；分别限幅或剔除范围外值 |
| `combine` / `add/subtract/multiply/divide/gt/ge/eq/and/or` | 恰好两个操作数，缺失传播；除零/溢出默认报错，显式 `invalid: nodata` 可剔除。逻辑按有限非零为真，结果 0/1；反转操作数可表达小于 |
| `combine` / `sum/mean/min/max/std/median` | 逐像元跨操作数统计，std 为总体标准差；`nodata: propagate/ignore`，默认 propagate。ignore 下 `min_valid` 默认 1，全缺失仍为 NoData |
| `combine` / `weighted_sum/weighted_mean` | `weights` 每操作数一个有限非负权重，至少一个正值；weighted_mean 仅用有效操作数的权重归一，零分母遵循 invalid |
| `combine` / `where` | 三操作数依次为条件、真分支、假分支；条件缺失则结果缺失，只检查实际选中分支的缺失。多条件可先比较、and/or，再 where；不用字符串表达式或任意代码执行 |

## 局部更新、矢量入格与波段

`update` 的 `method: assign/replace` 使用 `vector` 面掩膜及显式 `coverage: center/all_touched`。assign 的 `value` 为有限数或 null；replace 需要两个操作数，以第二个替换第一个，`replacement_nodata: propagate/keep` 默认 propagate。掩膜外逐像元保持来源值和缺失状态。栅格条件掩膜使用上面的 combine/where。

`update` 的 `method: fill` 需要至少两个操作数，按输入次序仅填当前缺失位置；零值不会触发借值。多个备用源全部缺失时保留 NoData，不进行插值或周边修补。

`rasterize` 参数为 `vector`、稳定 `id`、数值 `field`、`coverage` 及显式 `overlap: error/first/last`；可选 `layer/priority_field/background`。默认按字符串 ID 升序，提供 priority_field 后按其升序再按 ID；first/last 决定优先次序，不依赖文件记录顺序。默认背景 NoData；不继承参考栅格掩膜，仅使用其网格。

- center/all_touched 复用 Rasterio/GDAL 栅格化语义（线使用栅格化线段规则），支持点线面。同一像元被多对象选中时按 overlap 处理；error 在像元层面拒绝多对象，不只检查面是否几何重叠。
- fractional 仅接受投影面，按 Shapely 精确覆盖比例计算 `属性 × 占整像元比例`，未覆盖部分贡献为零；这是一种按整像元分母的覆盖加权值，不是覆盖部分的均值，也不能把总量属性直接当密度烧入。first/last 先在几何层处理重叠，分离的面可共同贡献一个像元；真实几何重叠在 error 下拒绝。面积按明确投影平面计算，不宣称椭球面积。

`bands` 按 operands 拆分、组合或重排：默认生成多波段 `result.tif`，`split: true` 输出 `band-1.tif` 等，顺序对应 operands，记录每份产物哈希。`calibrate: true` 明确应用各来源波段的 scale/offset，可由 operand 内同名参数覆盖；输出校准元数据重置为 1/0，避免重复应用，实际校准值写入记录。未开启 calibrate 时不接受额外 scale/offset。

## 区域总量分配与回算

`allocate` 第一操作数为有效域（仅使用其缺失掩膜，零有效），weighted 方法还需要第二个权重操作数。必需参数：

```json
{
  "vector": "/data/regions.gpkg",
  "id": "region_id",
  "total_field": "amount",
  "quantity": "total",
  "assumption": "redistribute_over_eligible_support",
  "method": "weighted",
  "weight_kind": "per_area",
  "exclude": "/data/exclusions.gpkg",
  "overlap": "error"
}
```

`exclude` 可省略；排除区先从来源面扣除，再做精确像元求交。需要适合任务范围的投影 CRS；复用已有单位转换计算平方米，地理 CRS 拒绝。来源量须有限非负且 ≤2^53，不把比率或类别作总量。

- `method: uniform` 在所有相交的有效像元间等份，局部相交也算一份；`area` 按有效相交面积；`weighted` 的 `weight_kind: per_area/per_pixel` 分别按权重×相交平方米或权重×覆盖比例。权重缺失不参与、零权重不分量、负权重拒绝。该接口明确把整份来源总量重分配到剩余有效支持区；存在正权重时，排除/图外部分不按来源面积扣走一份总量。需要扣除量时应先给出独立业务规则。
- 零权重或完全不可分配的来源保留全部 `unallocated`，来源不丢失。重叠面默认拒绝；显式 `overlap: independent` 分别分配，并在汇总栅格相加，保留独立来源贡献。每区输出原量、已分配、未分配、有效面积、原因、回读总量和守恒误差；门槛为 `max(1e-9, source_total*1e-12)`。
- `integer: true` 要求整数来源总量，先取各份额下整，再按小数余量递减补齐，平局按像元行列顺序。守恒按每个来源独立验证；整像元汇总超过 Float64 精确整数范围拒绝。普通 dtype 舍入不等价于这种守恒分配。

输出 `result.tif` 为每像元汇总总量，`contributions.tif` 每波段对应一个稳定来源 ID（波段描述及 record 一起保存），不可删除贡献文件。**共享边跨过像元时，汇总栅格无法单独反推各来源总量。** 回算须选择对应来源贡献波段求和；也可用原分区 all_touched 对对应波段执行已有 zonal CLI。不得对贡献值再次按覆盖比例加权，否则边界量会被重复折减。全部缺失来源在旧 zonal 中为 null，含义通过 allocation 的未分配记录解释。

## 网格变化下的守恒

`redistribute` 使用来源单波段、`reference` 目标网格、`quantity: total/density` 和 `assumption: uniform_within_source_pixel`。仅同一投影 CRS 的北向上规则网格；通过轴对齐像元精确矩形交集转移来源质量，支持非整数倍分辨率、偏移网格、裁短范围和有效零。拒绝比率、类别、负量及跨 CRS 变换，不调用普通重采样假冒守恒。

- total 为每像元总量；density 为每平方米密度，先乘来源像元平方米转总量，转移后除以完整目标像元平方米。输出 `coverage.tif` 记录每目标像元的已知来源面积比例；密度仅代表已知质量/完整像元面积，不估计缺失部分的真实值。
- 完全没有已知来源支持的目标像元为 NoData；部分覆盖不自动外推。落在目标范围外的已知量记入 unallocated；目标 reference 只指定网格，不施加其自身掩膜。原量=已转移+未转移，容差同 allocate；写后再次回算检查。没有椭球或跨投影守恒、分布式求解、业务权重验证；配方接入归下一阶段，不在此接口扩展。
