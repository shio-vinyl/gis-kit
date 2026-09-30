# 栅格配准与空间候选交接

由用户/上层调度指定的图像识读模型读取网线标值、本初子午线说明和交点，直接标注像素中心坐标；指定模型不可用时停止模型步骤，不静默回退。程序仅验证 actor 声明完整性，不选择模型或自动识别交点。无法辨认的点不加入拟合集。

首版只实现经纬网控制点的投影坐标仿射拟合、独立检查及无重采样 GeoTIFF 赋位。弯曲经纬网、严重纸张变形或无法说明坐标参考时，明确报告基线不适用，不自动选高阶多项式或 TPS，不假装已完成高精度纠正。

## 图框与质量门槛

每份控制点文件对应一个图框，以 `frame.id` 标识，`pixel_region` 为原图像素中心坐标下的 GeoJSON Polygon，可用孔洞排除移位插图。主图和不同尺度插图分别拟合；有效区域不得互相重叠。局部查看裁剪不构成新图框，也不改变原图坐标。

拟合前声明 `acceptance_policy`：检查点 RMSE、最大误差（目标投影 CRS 的单位）、拟合点凸包覆盖有效区域的最低面积比例，以及阈值依据 `rationale`。依据应说明用途、比例尺或图上线宽；程序不预设统一米数，也无法证明声明时间或点位识读正确。检查点须独立且分布合理，凸包覆盖比例不能证明内部控制密度充足。

缺少规则、检查误差超限或覆盖不足时输出 `hold` 和原因；满足声明条件输出 `pass`。报告保存拟合点凸包、覆盖率及凸包以外的外推区域（像素 Polygon/MultiPolygon），供叠加审查。缺失图框、跨图框控制点或非法阈值直接拒绝。旧控制点输入须先补图框；缺阈值可以拟合诊断，默认不能导出。

## 输入

```json
{
  "actor": {"model":"填写实际调用的模型标识","reasoning_effort":null},
  "source_sha256": "填写原图的 SHA256",
  "source_crs": "填写已核实的源坐标 CRS",
  "target_crs": "填写明确的投影 CRS",
  "coordinate_reference": "图上本初子午线、角度单位、基准的来源及未知项；任何近似假设均在此声明",
  "frame": {"id":"main","pixel_region":{"type":"Polygon","coordinates":[[[100,100],[900,100],[900,900],[100,900],[100,100]]]}},
  "acceptance_policy": {"max_check_rmse":2,"max_check_error":4,"min_fit_coverage":0.95,"rationale":"仅演示字段；实际阈值须按用途、比例尺和线宽预先确定"},
  "points": [
    {"id":"g1","pixel":[100,100],"world":[0,1000],"role":"fit"},
    {"id":"g2","pixel":[900,100],"world":[1000,1000],"role":"fit"},
    {"id":"g3","pixel":[100,900],"world":[0,0],"role":"fit"},
    {"id":"g4","pixel":[900,900],"world":[1000,0],"role":"check"}
  ]
}
```

world 遵循显式 source_crs：地理坐标为 `[longitude,latitude]`，投影坐标为 `[easting,northing]`。程序不推测旧本初子午线偏移；可使用已核实且支持相应子午线的 CRS，或先完成可追溯的经度换算。不得只换算巴黎经度就声称已验证 WGS84。源基准未知时，说明近似解释；不输出超出证据的精度结论。

拟合前固定 fit/check 划分；最低 3 个不共线拟合点及 1 个独立检查点仅为运行门槛，实际选点应覆盖四周和内部。记录图框、网线标注的局部查看证据。留出点不得根据拟合结果反复挑选，不把同一交点以不同 ID 放入两组。为控制过拟合，任何增加复杂变换的后续实现必须沿用固定检查点。

## 命令

```bash
python3 gis-kit/scripts/raster-georef.py fit /tmp/gcps.json /tmp/transform.json
python3 gis-kit/scripts/raster-georef.py preview /tmp/map-run/source.jpg /tmp/transform.json /tmp/gcp-overlay.png
python3 gis-kit/scripts/raster-georef.py attach /tmp/map-run/source.jpg /tmp/transform.json /tmp/referenced.tif
python3 gis-kit/scripts/raster-georef.py vectors /tmp/export/pixel-candidates.gpkg /tmp/transform.json /tmp/geographic-candidates.gpkg --manifest /tmp/export/export.json
```

示例只有三个拟合点，凸包覆盖率不足 0.95，会得到 `hold`；不要为通过验收事后放宽阈值。

报告单列 fit/check 的 RMSE、最大残差和目标坐标单位。`attach` 使用原图像素值和掩膜，赋予仿射与目标 CRS，不纠正重采样。转换包含从像素中心约定到 GDAL 像素角点约定的半像素修正。它的输出仍是候选，人工应结合原图和控制点位置审核。

向量导出先验证原图哈希一致，再变换已采样顶点。本页入口仅支持仿射。独立 TPS 栅格与已采样像素矢量候选交接见 [专业分析接口](professional-analysis.md#水系诊断与非线性研究)；保持双空间检查门槛，不能只变换贝塞尔控制柄，源空间采样界限不等同于目标空间曲线误差保证。

原图和像素几何永不覆盖。新配准另存 transform，可复用同一标注重新导出。控制点拟合残差和经纬网吻合只能说明图面坐标关系，不能证明历史对象的真实位置。

`preview` 在原图上画拟合点（红）、留出点（蓝）、反投影参考位置（青）与残差连接，需结合无叠加原图审核。输出只为视觉诊断，不改变控制点或配准参数。

## 导出与交接

`attach` 与 `vectors` 默认阻止 `hold` 导出。确需保留诊断候选时显式传 `--allow-hold`，清单仍保留 `hold` 及原因，图框隔离不可绕过。`attach` 保持整幅原图尺寸及像素值，但将有效区域外（含插图孔洞）的像素中心置为无效掩膜；它不裁剪或重采样。消费方必须遵守掩膜。

`vectors` 仅转换完全位于本图框内的对象；越界、穿越插图、空几何均整对象省略并记入清单，不自动裁剪、拆分或套用主图变换。不同图框各自导出，跨框对象须回到像素标注中明确拆分。完全没有可导出对象时拒绝写出。

每个地理成果旁生成 `<output>.manifest.json`：保存原图哈希、控制点输入及哈希、变换报告及哈希、门槛结论、有效区域和外推区域、最终文件哈希；矢量成果额外嵌入像素导出清单（标注版本、采样容差、遗漏面和诊断）、像素文件与清单哈希、本轮遗漏对象及未解决问题。旧像素清单没有诊断明细时保留未知值，不当作无问题。原始文件须随交接保留，绝对路径只作定位提示，哈希用于核验。

几何验收与历史语义分开：政体归属、时间范围、来源角色及数据库入库规则由使用项目定义，本 skill 不代为确认。

多图框整包回读、固定检查点版本位移比较和可搬移交接见 [重复交付接口](repeated-delivery.md#仿射版本比较与交接georef-deliverypy)。复用本页仿射门槛，不改变候选属性。
