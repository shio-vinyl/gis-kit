# 标注对象、增量与记录契约

此处维护 `raster-annotate.py` 的数据格式；执行顺序见 [标注流程](raster-annotation.md)。所有坐标是有限数值对，默认原图像素中心。

## 最小 patch

```json
{
  "base_revision": 0,
  "mode": "append",
  "actor": {"model":"填写实际调用的模型标识", "reasoning_effort":null},
  "put": {
    "nodes": {"n1":{"xy":[10,10]}, "n2":{"xy":[80,10]}},
    "edges": {
      "e1": {"start":"n1", "end":"n2", "kind":"administrative", "status":"visible",
             "geometry":{"type":"polyline", "vertices":[[40,12]]}}
    }
  }
}
```

`put` 按对象组、ID 组织；仅发送本次涉及对象。`base_revision` 必须等于当前版本。可带 `view_id` 将本次所有新增/修订坐标从该视图换算到原图，不能混用两个坐标空间；引用已有节点时只写 ID，不重复坐标。视图必须属于当前版本。

模型由用户/上层调度选择并执行，指定模型不可用时停止，不静默回退。actor 记录实际模型和推理设置；没有对应设置或无法取得时显式写 null。程序校验声明完整性，不替调用者验证真实模型身份。新会话仅记录调用者负责选择的 policy，不预填任何模型；已有历史声明保持原样。

| mode | 允许操作 |
|---|---|
| `draft` | 仅 revision 0 的初稿 |
| `append` | 仅新增 ID，可引用旧节点/边；拒绝覆盖旧对象、空提交、delete、splice 和 crossings 声明 |
| `shape` | 仅修改已有边的 geometry；节点、边集合、属性、点、面和连接全部冻结 |
| `topology` | 单独审核后的节点、连接、属性、点或面调整；同步维护所有引用 |

`put` 替换指定对象整体；`delete: {"edges":["e1"]}` 删除指定对象。所有请求（含被拒绝请求）、版本和视图独立保存，拒绝过期提交，不覆盖原始 patch。

## 几何对象

- `nodes`：`{"xy":[x,y]}`，供边引用的接点。`points`：例如 `{"xy":[x,y],"names":["原文名称"],"status":"uncertain","note":"符号不清"}`；定位物理符号中心，聚落点不自动成为连接节点。
- `edges`：`start/end` 必须引用节点 ID。`kind` 区分行政界、海岸等；`status` 为 `visible/inferred/uncertain`，证据状态改变处拆边。`polyline.vertices` 只含内部顶点，端点来自共享节点，保留真实折角。
- 原生三次曲线：`{"type":"cubic","segments":[{"c1":[x,y],"c2":[x,y],"end":[x,y]}]}`。控制柄由模型直接选择，程序只采样，不从折线拟合。最后一段 end 可省略而引用终点；显式提供时须精确等于共享终点。

## 共享边组面与连接

```json
{
  "status":"candidate",
  "name":"区域原文",
  "outer":[{"edge":"e1"},{"edge":"e2"},{"edge":"e3","reverse":true}],
  "holes":[]
}
```

该对象放入 `put.faces`。外环 `outer` 和各孔洞环按顺序引用有向边，反向用 `reverse:true`；所有接点和首尾必须共享节点 ID。相邻面引用同一条界，不按重合坐标自动吸附。上述示例须已有能闭合的 e1/e2/e3，程序不补缺边。

多个不相连部分使用多个 face，以 `region_id` 关联。缺边面用 `status: incomplete` 并记录 `missing`，保留已知边；其他面状态为 `candidate` 或 `uncertain`。程序只导出闭合有效面，不自动从整个线网发现面、填洞或推断地区归属。

真实交汇须拆边并共享节点。交叉不连接可在拓扑提交中声明 `crossings: [{"edges":["e1","e2"],"xy":[x,y],"relation":"disconnected"}]`，疑义用 `uncertain`。该字段整体替换当前 crossings；修改时保留其他已有声明。

## 局部折线修订

`splice: [{"edge":"e1","start":3,"delete_count":2,"vertices":[[x,y],[x,y]]}]` 的索引从 0 开始，只针对 `geometry.vertices` 内部顶点。各窗口均以 base revision 为基准，程序按逆序应用；窗口不得重叠，也不能与同一边的 put 混用。

`review` 返回的窗口 before/after 为冻结邻点，不写入替换 vertices。形状修订不改变共享节点；节点位置或连接顺序有误时，另做 topology 修订，不用移动内部点规避固定端点。

## 会话记录

```json
{
  "model":"填写实际调用的模型标识", "reasoning_effort":null,
  "status":"completed", "viewed_ids":[],
  "elapsed_seconds":null, "revision_count":null,
  "stage_seconds":{"acquisition":null,"image_review_and_annotation":null,"cli":null,"export":null},
  "usage":{"input_tokens":null,"output_tokens":null,"cached_input_tokens":null,"reasoning_output_tokens":null}
}
```

用 `session RUN RECORD.json` 追加。失败/中止分别记录 `failed/aborted`，不得遗漏。调度设置另留证据，actor 声明无法证明实际模型。`viewed_ids` 仅填真正看过的工作台视图；同一视图原图/叠加分别查看时，另记逐文件查看事件和次数。外部图像保留路径及 hash，不虚构工作台 view_id。

渐进绘制附记录实际查看文件、base revision、patch 路径、返回 revision、事件顺序及未解决范围；保存原图、辅助图、原始输出和各轮修订。时间需真实起止读数，缺项记 null；CLI 时间与代理阶段区分，重叠阶段不重复相加。token 只记实际返回值；缓存输入、思考输出为子集，不再次相加。仅保留结构化运行信息，不存凭据、敏感原始日志或隐藏推理。
