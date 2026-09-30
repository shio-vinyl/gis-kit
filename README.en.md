<div align="center">

# gis-kit

**Everyday GIS operations as scripts, easy for AI agents to call.**

A skill for Claude Code, Codex and other agents.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](requirements-tested.txt)
[![Tests](https://github.com/shio-vinyl/gis-kit/actions/workflows/tests.yml/badge.svg)](https://github.com/shio-vinyl/gis-kit/actions/workflows/tests.yml)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-SKILL.md-8A2BE2.svg)](SKILL.md)

[中文](README.md) · English

</div>

---

## What this is

This is the GIS toolbox I use myself: operations I find useful, written up as scripts the way I understand them, now more than 40 of them. What sets it apart from an ordinary script collection is that it's set up for agents to call: a single entry point lists and searches the tools, each tool's `--help` is its parameter reference, and the agent can look things up and combine them on its own.

```bash
python scripts/gis.py list                # tool catalogue (standard library only)
python scripts/gis.py list --search raster
python scripts/gis.py raster --help       # parameter reference
```

[SKILL.md](SKILL.md) only lays down a few basic judgements for handling data, such as CRS, units, NoData, and marking inferred results as `candidate` / `hold`. Which tools to use and how to chain them is up to the agent.

For ordinary-sized tasks this is enough. When the data is very large or performance matters, have the agent call a desktop GIS backend such as QGIS or GRASS; big tables can also go through the optional DuckDB spatial SQL path.

More practical features for specific scenarios, and some research-oriented ones, will be added over time.

## Current tools

- **Vector**: clip, dissolve, merge, spatial join, buffer, field and topology checks and fixes, area and urban metrics, packaged as versioned daily result bundles.
- **Raster and terrain**: zonal statistics, reprojection, COG output, windowed processing for large rasters; DEM slope, aspect, contours and profiles; optional GRASS for hydrology, viewsheds and terrain cost.
- **Networks and coverage**: directed road networks, OD, facility coverage scenarios, demand-weighted coverage. Without demand weights, it won't make up a population coverage figure.
- **Historical map vectorization**: still under research and not yet mature. The model looks at the scanned image and traces roads and boundaries section by section in pixel space; the scripts handle cropping, non-generative enhancement, coordinate conversion, versioning and overlay checks, then affine or TPS georeferencing. No generative infill, and no automatic line tracing standing in for the model's own reading. At the moment only the astra6.0 and sol6.1 model series run it reliably; I recommend low reasoning effort for both, since raising it doesn't noticeably improve results.
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

**3. Just ask the agent**

> Dissolve `parcels.gpkg` by land-use type, compute areas in a CGCS2000 projection, and give me a QA plot.
>
> Compute slope and aspect from this DEM and report mean slope for each district.
>
> Work out 15-minute drive-time coverage for these hospitals and list the neighbourhoods left uncovered.
>
> Trace the roads on this scanned historical map and georeference it with my control points.

The agent reads [SKILL.md](SKILL.md), consults the relevant `references/` documents as needed, and picks the tools and parameters itself.

## Environment and dependencies

Dependencies come in layers: `requirements-core.txt` covers vector, raster and QA plots; `requirements-full.txt` adds networks, statistics, image enhancement and tests; `requirements-tested.txt` pins the exact versions that pass the full suite on Python 3.10; `requirements-spatial-sql.txt` is the optional DuckDB layer. On platforms where pip struggles with GDAL-related packages, use `environment.yml` with conda-forge.

The agent should always use the same interpreter; see [Runtime environment](references/runtime-environment.md). QGIS, GRASS and PySAL are optional backends that need their own installation. Chinese map labels use the first installed font from the list in `config/user.yaml`, Noto Sans CJK SC by default; if none is installed it falls back to DejaVu Sans, and Chinese text won't render.

Finished maps, Scene / Storyboard and map videos are handled by a separate project, gis-composer. It isn't public yet; I want to polish it for a while longer before releasing it. gis-kit doesn't depend on it and produces diagnostic and QA plots without it; if you do have it, it's located through `GIS_COMPOSER_HOME`.

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
