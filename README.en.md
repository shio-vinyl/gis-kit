<div align="center">

# gis-kit

**A GIS toolbox for AI agents.**

Vector, raster, DEM terrain, network accessibility, and tracing and georeferencing scanned maps. Everyday GIS work, handed to Claude Code, Codex and similar agents to finish on the command line.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](requirements-tested.txt)
[![Tests](https://github.com/shio-vinyl/gis-kit/actions/workflows/tests.yml/badge.svg)](https://github.com/shio-vinyl/gis-kit/actions/workflows/tests.yml)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-8A2BE2.svg)](SKILL.md)

[中文](README.md) · English

</div>

---

## How this started

Anyone who does GIS knows the moment: you just want to dissolve a layer by a field, check its CRS, or compute slope from a DEM, and instead you are opening a desktop GIS, waiting for it to load, adding layers and digging through menus for the right tool. The job takes five minutes; the ceremony takes half of that.

gis-kit grew out of that. I took the operations I find useful but never want to launch QGIS for, wrote them up as scripts one by one, and handed them to an agent. There are now more than 40 tools covering most of what I run into day to day. More will keep going in: practical features for specific scenarios, and some more research-oriented experiments.

## A toolbox, not a rulebook

A lot of agent skills are written as rules for the model: do this first, then that, and in this situation you must do the other thing, with the whole procedure fixed in the prompt. gis-kit goes the other way. It assumes today's models are capable enough to understand a task, look things up and plan on their own, so what it mainly provides is tools:

```bash
python scripts/gis.py list                # see what's in the box (standard library only)
python scripts/gis.py list --search raster
python scripts/gis.py raster --help       # each tool's own parameter help
```

The agent browses the catalogue, reads the help, and decides which pieces to use and how to chain them. Each tool only has to do its own part well: deterministic computation, atomic writes, a read-back after writing, and a QA plot when one is useful.

The rules haven't vanished entirely. They stay where the data itself tends to mislead: a CRS label is not a reprojection, degrees are not metres, NoData must not leak into statistics, anything inferred is marked `candidate`, anything uncertain is marked `hold`, and none of it is reported as settled fact. These hold no matter how strong the model is. They live in [SKILL.md](SKILL.md), and they are the only conventions an agent needs to keep while using the toolbox.

## When to open a desktop GIS anyway

gis-kit is aimed at everyday scale: a few layers, one DEM, one city's road network. When the data is genuinely large or performance really matters, the better move is to have the agent call a professional backend such as QGIS or GRASS, or use the optional DuckDB spatial SQL path for big tables. gis-kit isn't trying to replace a desktop GIS. It fills the wide space between "dash off some GeoPandas" and "open the full desktop application".

## What's in the box

- **Vector**: clip, dissolve, merge, spatial join, buffer, field and topology checks and fixes, area and urban metrics, packaged as versioned daily result bundles.
- **Raster and terrain**: zonal statistics, reprojection, COG output, windowed processing for large rasters; DEM slope, aspect, contours and profiles; optional GRASS for hydrology, viewsheds and terrain cost.
- **Networks and coverage**: directed road networks, OD, facility coverage scenarios, demand-weighted coverage. Without demand weights, it won't make up a population coverage figure.
- **Scanned maps**: my own favourite part. The model looks at the image and traces roads and boundaries section by section in pixel space; the scripts handle cropping, non-generative enhancement, coordinate conversion, versioning and overlay checks, then affine or TPS georeferencing. No generative infill, and no automatic line tracing standing in for the model's own reading.
- **Repeated delivery**: `--trace` records the steps you actually ran, which can be turned into a recipe and replayed on the next batch of data; `semantic-check` tells you whether selected fields were quietly changed between steps.

## Quick start

**1. Install**: clone into your agent's skills directory and keep the directory name `gis-kit`.

```bash
# Claude Code
git clone https://github.com/shio-vinyl/gis-kit.git ~/.claude/skills/gis-kit
# Codex
git clone https://github.com/shio-vinyl/gis-kit.git ~/.codex/skills/gis-kit
```

**2. Set up Python** (Python ≥ 3.10)

```bash
python3.10 -m venv ~/.venvs/gis-kit
~/.venvs/gis-kit/bin/python -m pip install -r ~/.claude/skills/gis-kit/requirements-full.txt
~/.venvs/gis-kit/bin/python ~/.claude/skills/gis-kit/scripts/daily.py environment
```

**3. Then ask the way you'd brief a colleague**

> Dissolve `parcels.gpkg` by land-use type, compute areas in a CGCS2000 projection, and give me a QA plot.
>
> Compute slope and aspect from this DEM and report mean slope for each district.
>
> Work out 15-minute drive-time coverage for these hospitals and list the neighbourhoods left uncovered.
>
> Trace the roads on this scanned historical map and georeference it with my control points.

The agent takes it from there: it reads [SKILL.md](SKILL.md), pulls in whichever `references/` documents it needs, and picks the tools and parameters itself.

## Environment and dependencies

Dependencies come in layers: `requirements-core.txt` covers vector, raster and QA plots; `requirements-full.txt` adds networks, statistics, image enhancement and tests; `requirements-tested.txt` pins the exact versions that pass the full suite on Python 3.10; `requirements-spatial-sql.txt` is the optional DuckDB layer. On platforms where pip struggles with GDAL-related packages, use `environment.yml` with conda-forge.

The agent should always use the same interpreter; see [Runtime environment](references/runtime-environment.md). QGIS, GRASS and PySAL are optional backends that need their own installation. Chinese map labels use the first installed font from the list in `config/user.yaml`, Noto Sans CJK SC by default; if none is installed it falls back to DejaVu Sans, and Chinese text won't render.

Finished maps, Scene / Storyboard and map videos belong to the separate gis-composer runtime, located through `GIS_COMPOSER_HOME`. gis-kit doesn't depend on it and produces diagnostic and QA plots without it.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests
```

`tests/test_*.py` are self-contained regression tests on synthetic or hand-computed data. `tests/run_*_acceptance.py` run full chains on real files and need external data you supply yourself (OSM, Natural Earth, a DEM and so on), with output directories outside the repository; each script lists its inputs at the top.

## Data and privacy

The scripts run locally, but whatever the agent reads may end up in a remote model's context. Traces from `--trace` include paths and task parameters, so look them over before sharing. The repository ships no third-party datasets; if you use OSM (ODbL), Natural Earth (public domain) or similar data, follow their licence and attribution terms.

## License

[Apache License 2.0](LICENSE)

<sub>Keywords: GIS · geospatial · AI agent · Claude Code skill · Codex skill · Agent Skills · LLM tools · GeoPandas · Rasterio · GDAL · Shapely · DuckDB · DEM · terrain analysis · network analysis · accessibility · georeferencing · map digitization · spatial analysis</sub>
