# 栅格整理、类别统计与派生结果

统一入口 `raster.py OP INPUT... --params params.json --output /work/new-bundle`。复用数值栅格的网格、波段、NoData、类型、逐块解码回读与新目录发布契约。所有输入保持只读；修补成果不能替换原始观测、扫描原图或标注证据层。

## 范围与共同参数

要求自包含、已知 CRS、北向上栅格；多个输入必须 CRS、原点、分辨率和宽高完全一致，错格先显式 align。`band` 默认 1；NoData、内部掩膜及非有限值统一缺失，有效 0 保留。类别值要求精确整数，类别 0 是普通类别。面积与米距离要求可验证的投影单位；面积为投影平面平方米，不做地面比例尺改正或椭球面积。

默认 `max_elements=10000000` 限制输入数组元素数量，沿用 `max_pixels` 上限；全量读入有界数组，不声称流式内存。邻域统计按 `block_size`（默认 256）逐输出窗口加 halo，跨块与全量结果相同；连通标记与借值为全局操作，`block_size` 在这些操作中仅改变写后回读窗口，不进行块内独立标记。`max_window_cells=10001` 限制邻域，`max_donor_pairs=2000000` 限制空洞借值候选总数，`max_intersections=2000000` 限制精确覆盖求交。

修补/邻域/碎斑/形态操作可传 `protected` 与 `scope` 栅格路径：保护图非零或缺失处不改；scope 只有有限非零处允许改变。填洞与碎斑借值也限于 scope 且不使用受保护像元；保护范围须由调用者提供，程序不会识别业务重要岛屿。它们不作为地形障碍或拓扑借值屏障，距离按像元中心直线测量。

产生 `modified.tif`（0 未改，1 已改）、`modified_pixels`、`interpolated_pixels`。原缺失变为有限值单独计数。类别编号、区域编号、变化码均为本次输入的派生标识，不是跨版本永恒对象 ID。全部操作记录适用范围、源指纹、参数、SciPy/运行环境与实现指纹。

## 整理与邻域

- **`fill_holes`**：必填 `kind:continuous|categorical`、`method:nearest|idw`、`max_hole_pixels`、`max_distance_m`。`connectivity:4|8` 默认 4，按原始缺失连通分量判断；接触栅格外边缘、超尺寸、含保护像元或越 scope 的整个空洞不填。每个符合条件像元仅借用原输入有效像元，不级联借用新值；无距离内供体继续缺失。连续可选最近邻或距离平方反比，类别只允许最近邻。最近等距按原始行列顺序破平局。距离门槛可能使空洞仅部分填补，修补掩膜保留真实范围。
- **`focal`**：显式 `kind`，`statistic:mean|sum|min|max|median|std|mode`（std 为总体标准差）；类别仅 mode，众数等频取最小类别。使用奇数 `size` 方窗，或 `radius_m` 像元中心圆形范围，非方像元按实际水平距离。`min_coverage` 在 [0,1]，默认 1。`boundary:partial` 按实际图内邻域计覆盖；`pad` 将图外当缺失计入分母；`truncate` 丢弃不完整边缘窗口。原缺失默认继续缺失，只有显式 `derive_missing:true` 才允许生成邻域派生值，并记录插补标记。
- **`aggregate`**：必填 `factor`、`kind`、`statistic`、`boundary:partial|pad|truncate`，支持相同统计量和有效覆盖门槛。原点不变，像元边长乘 factor。partial 保留不足块并按实际图内大小计覆盖；pad 保留不足块但按完整块大小计覆盖；truncate 删除右/下不足块。保留不足块时输出末格外边界可能扩展到原图外，`coverage.tif` 明确有效比例，不伪造图外样本。普通 sum 为有效值总和，非守恒换格；总量跨格网转移使用 E 的 redistribute。

## 斑块与形态

`regions` 对每个类别分别用 `connectivity` 标记，输出实例栅格及 `instances` 对照表（类别、像元数、平方米）；同类不同位置保持独立实例。实例顺序为类别升序、首次行列出现位置。

`sieve` 必填 `min_area_m2`，严格小于门槛的实例才进入整理；含任一 protected 像元的整个实例保留。`method:remove` 把可修改小斑块变为缺失；`method:nearest` 另必填 `max_distance_m`，借最近非小斑块类别。无合格供体时保留原值，原缺失始终不填；等距由已记录 SciPy EDT 行为决定，不承诺不同 SciPy 版本同一平局输出。门槛为 0 时不删除实例。

`seed_region` 的 `seeds:[[row,column],...]` 从零开始，必须落于有效像元；保留其所属完整实例，其余位置输出缺失。四/八连通使用同一结构定义。

`morphology` 必填 `category`、`background`（互异整数）与 `method:dilate|erode|open|close`，邻域同 focal，`iterations` 默认 1。仅目标类/背景类的未保护、scope 内有效像元可变；其他类别及缺失孔洞不改。结构运算的图外视为背景；受保护小岛应覆盖完整对象，程序不自动推断对象重要性。内部形态候选可能跨过缺失邻域影响其邻近有效像元，未知像元本身永远不填。

## 类别、变化与分区回填

`class_area` 需要 `vector`、稳定 `id` 与 `method:center|all_touched|fractional`。`table.json` 和记录含每区×类别平方米、有效面积、图内采样支持面积、有效覆盖率及完整多边形面积。fractional 精确求平面像元交叠；center/all_touched 为所选像元面积，不能冒充精确边界面积。覆盖率分母是区在当前栅格内的采样支持，图外范围可由完整多边形面积与支持面积辨识。区域可以重叠，逐区独立统计，不把重叠行相加当作无重叠总体。

`transition` 必须恰好两期类别栅格；`result.tif` 为类别对编码，`codebook` 为有序类别对、像元数及面积，`change.tif` 为 0 相同/1 变化。仅双期有效像元进入矩阵；第一期独有、第二期独有、双期未知面积分别输出。单期新获得值不能自动算类别变化。

`class_combine` 接受多个栅格，联合有效像元的类别元组按字典序编码，输出栅格和 codebook；任何输入缺失均继续未知。输入顺序是类别元组维度顺序，不可悄悄重排。

`zonal_fill` 与 class_area 使用相同 vector/id/method；`statistic:mean|sum`，mean 按覆盖比例加权，sum 为覆盖比例加权值总和。只回填原有效像元，未知处不插补；任何两个区的写入像元支持交叠均拒绝，含共享部分像元，避免暗中设置优先级。全部无效区的统计值为 null。

上述统计量、覆盖、未知与插补身份也保留在 [顺序配方](repeated-delivery.md) 的原步骤记录和结果档案中。
