<div align="center">

# gis-kit

**给 AI agent 的一只 GIS 工具箱。**

矢量、栅格、DEM 地形、路网可达、扫描地图描绘与配准。日常的 GIS 活儿，交给 Claude Code、Codex 这类 agent 在命令行里做完。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](requirements-tested.txt)
[![Tests](https://github.com/shio-vinyl/gis-kit/actions/workflows/tests.yml/badge.svg)](https://github.com/shio-vinyl/gis-kit/actions/workflows/tests.yml)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-8A2BE2.svg)](SKILL.md)

中文 · [English](README.en.md)

</div>

---

## 缘起

做 GIS 的人大概都有这种时刻：只是想把一个图层按字段融合一下、看一眼它的 CRS、给 DEM 算个坡度，却要打开桌面 GIS，等它启动，加载图层，在菜单里翻半天找工具。

gis-kit 就是从这里长出来的。我把自己平时觉得有用、但每次都不想为它专门开 QGIS 的那些操作，一个个写成脚本搬了进来，然后交给 agent 去用。现在它有 40 多个工具，覆盖了我日常遇到的大部分场景。接下来还会继续往里添东西，一类是特定场景下的实用功能，另一类是偏研究性的尝试。

## 给工具箱，不给规矩

很多 agent skill 的写法是给模型立规矩：先做什么、再做什么、遇到某种情况必须怎样，把一套流程写死在提示词里。gis-kit 换了个方向。它假定今天的模型已经足够强，能自己理解任务、查资料、做规划，所以这里提供的主要是工具：

```bash
python scripts/gis.py list                # 看看箱子里有什么（只需标准库）
python scripts/gis.py list --search raster
python scripts/gis.py raster --help       # 每个工具自己的参数说明
```

agent 自己翻目录、读 help，决定用哪几件、按什么顺序拼起来。每件工具只管把自己那部分做扎实：计算是确定性的，文件原子写出，写完回读一遍，需要时出一张检查图。

规矩没有完全消失，只留在数据本身容易骗人的地方：CRS 标签不等于重投影，经纬度不是米，NoData 不能混进统计，推定出来的东西标成 `candidate`，拿不准的标成 `hold`，不把它们写成确定结论。这几条和模型强不强无关，写在 [SKILL.md](SKILL.md) 里，是 agent 用这套工具时唯一需要守的约定。

## 什么时候还是该开桌面 GIS

gis-kit 瞄准的是日常规模：几个图层、一块 DEM、一个城市的路网。数据量真的很大，或者对性能要求很高时，更合适的做法是让 agent 调用专业后端，比如 QGIS、GRASS，或者用可选的 DuckDB 空间 SQL 处理大表。gis-kit 自己不打算变成一个桌面 GIS 的替代品。它更像是在“随手写段 GeoPandas”和“打开完整桌面软件”之间，补上中间那一大片。

## 箱子里现在有什么

- **矢量**：裁剪、融合、合并、空间关联、缓冲、字段与拓扑的检查修复、面积与城市指标，结果打包成带版本的日常结果包。
- **栅格与地形**：分区统计、重投影、COG 输出，大栅格按窗口处理；DEM 的坡度、坡向、等高线、剖面；可选接 GRASS 做水文、可视域和地形成本。
- **路网与覆盖**：有向路网、OD、设施覆盖情景、需求加权覆盖。没有需求权重时，它不会硬报一个人口覆盖率。
- **历史地图矢量化**：这一块还在研究中，并不完善。模型直接看扫描图，在像素空间里一段一段地描出道路、边界；程序负责裁剪、非生成式增强、坐标换算、版本管理和叠加检查，最后做仿射或 TPS 配准。全程不做生成式补图，也不用程序自动追线去替模型“看”。目前能稳定运作的只有 astra6.0 和 sol6.1 系列模型，推荐两者都用 low 思考强度，调高强度对效果的提升并不明显。
- **重复交付**：用 `--trace` 记下一次实际跑过的步骤，整理成 recipe，下次换一批数据照着再跑一遍；`semantic-check` 可以检查某些字段在步骤之间有没有被悄悄改掉。

## 快速开始

**1. 安装**：克隆到 agent 的 skills 目录，目录名保持 `gis-kit`。

```bash
# Claude Code
git clone https://github.com/shio-vinyl/gis-kit.git ~/.claude/skills/gis-kit
# Codex
git clone https://github.com/shio-vinyl/gis-kit.git ~/.codex/skills/gis-kit
```

**2. 准备 Python 环境**（Python ≥ 3.10）

```bash
python3.10 -m venv ~/.venvs/gis-kit
~/.venvs/gis-kit/bin/python -m pip install -r ~/.claude/skills/gis-kit/requirements-full.txt
~/.venvs/gis-kit/bin/python ~/.claude/skills/gis-kit/scripts/daily.py environment
```

**3. 然后就像跟同事交代活儿一样说**

> 把 `parcels.gpkg` 按用地类型融合，用 CGCS2000 投影算面积，出一张检查图。
>
> 算出这块 DEM 的坡度和坡向，统计每个行政区的平均坡度。
>
> 给这几个医院算 15 分钟车程覆盖，列出覆盖不到的小区。
>
> 把这张扫描老地图上的道路描出来，用我给的控制点配准。

剩下的交给 agent：它会读 [SKILL.md](SKILL.md)，按需翻 `references/` 里的参考文档，自己挑工具、定参数。

## 环境与依赖

依赖分了几层：`requirements-core.txt` 够做矢量、栅格和检查图；`requirements-full.txt` 再加上路网、统计、图像增强和测试；`requirements-tested.txt` 是 Python 3.10 下全部测试通过的精确版本；`requirements-spatial-sql.txt` 是可选的 DuckDB。pip 装 GDAL 相关包吃力的平台，可以用 `environment.yml` 走 conda-forge。

agent 运行时要始终用同一个解释器，细节见[运行环境](references/runtime-environment.md)。QGIS、GRASS、PySAL 属于可选后端，需要单独安装。中文图面按 `config/user.yaml` 里的顺序选第一个装了的字体，默认 Noto Sans CJK SC；一个都没有时会回退到 DejaVu Sans，这时中文会显示不出来。

正式出图、Scene / Storyboard 和地图影片由另一个项目 gis-composer 负责。它暂时还没公开，我想再打磨一阵子再放出来。gis-kit 不依赖它，没有它也照样能出诊断图和检查图；装了的话通过 `GIS_COMPOSER_HOME` 定位。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests
```

`tests/test_*.py` 是自包含的回归测试，用的都是合成数据或手算数据。`tests/run_*_acceptance.py` 用真实文件跑完整链路，需要自己准备 OSM、Natural Earth、DEM 等外部数据，输出目录放在仓库之外，每个脚本开头都写了需要哪些输入。

## 数据与隐私

脚本在本地跑，但 agent 读到的内容可能会进入远端模型的上下文。`--trace` 记下的轨迹里有路径和业务参数，分享前先看一遍。仓库不附带任何第三方数据；用 OSM（ODbL）、Natural Earth（公有领域）等数据时，请遵守各自的许可和署名要求。

## 许可证

[Apache License 2.0](LICENSE)

<sub>关键词：GIS · geospatial · AI agent · Claude Code skill · Codex skill · Agent Skills · LLM · GeoPandas · Rasterio · GDAL · Shapely · DuckDB · DEM · terrain analysis · network analysis · georeferencing · 空间分析 · 地理信息 · 地图配准</sub>
