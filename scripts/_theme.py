# /// script
# dependencies = [
#   "matplotlib>=3.8",
#   "pyyaml>=6.0",
# ]
# ///

from __future__ import annotations

from _sandbox import configure_writable_caches

configure_writable_caches()

import functools
from pathlib import Path

import matplotlib.font_manager as fm
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_GIS_DIR = _SCRIPTS_DIR.parent

_DEFAULT_CONFIG: dict = {
    "defaults": {"crs": "EPSG:4326", "output_format": "gpkg", "encoding": "utf-8"},
    "font": {
        "family": "Noto Sans CJK SC",
        "fallback": ["Source Han Sans SC", "PingFang SC", "Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei"],
        "size": {"title": 16, "label": 10, "tick": 8},
    },
    "plot": {"dpi": 300, "figsize": [12, 8], "export_format": "png"},
}


def _find_gis_root() -> Path:
    for parent in [_SCRIPTS_DIR, *_SCRIPTS_DIR.parents]:
        candidate = parent / "config" / "user.yaml"
        if candidate.exists():
            return parent
    return _GIS_DIR


def load_user_config() -> dict:
    root = _find_gis_root()
    config_path = root / "config" / "user.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return dict(_DEFAULT_CONFIG)


def load_template(category: str, style: str = "default") -> dict:
    root = _find_gis_root()
    template_path = root / "templates" / category / f"{style}.yaml"
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    with open(template_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_palette(name: str) -> list[str]:
    root = _find_gis_root()
    palette_path = root / "templates" / "colors" / "palettes.yaml"
    if not palette_path.exists():
        raise FileNotFoundError(f"Palette file not found: {palette_path}")
    with open(palette_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    palettes = data.get("palettes", {})
    if name not in palettes:
        raise KeyError(f"Palette '{name}' not found. Available: {list(palettes.keys())}")
    return palettes[name]


@functools.lru_cache(maxsize=1)
def get_chinese_font() -> str:
    """Return the first installed font from config/user.yaml, then built-in CJK defaults."""
    font_cfg = load_user_config().get("font") or {}
    default_cfg = _DEFAULT_CONFIG["font"]
    candidates = [font_cfg.get("family"), *(font_cfg.get("fallback") or []),
                  default_cfg["family"], *default_cfg["fallback"]]
    available = {f.name for f in fm.fontManager.ttflist}
    for font in candidates:
        if font and font in available:
            return font
    return "DejaVu Sans"


def apply_theme(fig, ax) -> None:
    font_name = get_chinese_font()
    config = load_user_config()
    font_cfg = config.get("font", _DEFAULT_CONFIG["font"])

    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.grid(True, color="#EAECEE", linewidth=0.5, linestyle="--", alpha=0.7)
    ax.set_axisbelow(True)

    for spine in ax.spines.values():
        spine.set_color("#CCCCCC")
        spine.set_linewidth(0.5)

    title = ax.get_title()
    if title:
        ax.set_title(title, fontsize=font_cfg["size"]["title"], fontname=font_name)
    ax.xaxis.label.set_fontname(font_name)
    ax.yaxis.label.set_fontname(font_name)
    ax.xaxis.label.set_fontsize(font_cfg["size"]["label"])
    ax.yaxis.label.set_fontsize(font_cfg["size"]["label"])
    ax.tick_params(labelsize=font_cfg["size"]["tick"])
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontname(font_name)

    fig.tight_layout()


def apply_chart_style(fig, ax, template: dict | None = None) -> None:
    if template is None:
        template = load_template("chart", "default")

    apply_theme(fig, ax)

    style = template.get("style", {})
    layout = template.get("layout", {})
    font_name = get_chinese_font()

    bg = style.get("background", "#FFFFFF")
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    grid_cfg = style.get("grid", {})
    if grid_cfg.get("enabled", True):
        ax.grid(
            True,
            color=grid_cfg.get("color", "#EAECEE"),
            linewidth=grid_cfg.get("linewidth", 0.5),
            linestyle=grid_cfg.get("linestyle", "--"),
        )

    title_cfg = layout.get("title", {})
    title = ax.get_title()
    if title:
        ax.set_title(
            title,
            fontsize=title_cfg.get("fontsize", 14),
            fontweight=title_cfg.get("fontweight", "bold"),
            color=title_cfg.get("color", "#2C3E50"),
            fontname=font_name,
        )

    label_cfg = layout.get("axis_label", {})
    ax.xaxis.label.set_fontsize(label_cfg.get("fontsize", 11))
    ax.xaxis.label.set_color(label_cfg.get("color", "#566573"))
    ax.yaxis.label.set_fontsize(label_cfg.get("fontsize", 11))
    ax.yaxis.label.set_color(label_cfg.get("color", "#566573"))

    tick_cfg = layout.get("tick", {})
    ax.tick_params(
        labelsize=tick_cfg.get("fontsize", 9),
        labelcolor=tick_cfg.get("color", "#7F8C8D"),
    )

    legend = ax.get_legend()
    if legend:
        legend_cfg = layout.get("legend", {})
        legend.set_frame_on(True)
        legend.get_frame().set_alpha(legend_cfg.get("framealpha", 0.9))
        for text in legend.get_texts():
            text.set_fontsize(legend_cfg.get("fontsize", 9))
            text.set_fontname(font_name)

    fig.tight_layout()
