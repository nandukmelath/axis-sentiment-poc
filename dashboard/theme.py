"""Hex (hex.tech) design system, ported to Streamlit.

Tokens are the ACTUAL values lifted from hex.tech's stylesheet — both their
`[data-theme='light']` (their default) and `[data-theme='dark']` blocks.

LIGHT (default, what hex.tech ships):
  bg300 #fcf8f8 · bg400 #f9f1f1 · bg500 #ebcccc     <- warm blush off-white
  grid ramp #efebed → #afa9b1                        <- hairline borders
  text #31263B (eggplant) · muted #89828d · loud #14141C (obsidian)

DARK:
  bg300 #0f0f15 · bg400 #111118 · bg500 #14141c
  grid ramp #252128 → #6a565b
  text #F5C0C0 (roseQuartz) · muted #99797d · loud #ffffff

Brand colours are identical across themes (that's their system):
  obsidian #14141C · roseQuartz #F5C0C0 · amethyst #A477B2 · minsk #473982
  eggplant #31263B · jade #5CB198 · citrine #CDA849 · opal #FBF9F9
  violetTopaz #5F509D · cement #717A94

Their design methodology, reproduced:
  1. surfaces separated by HAIRLINE 1px grid borders, not shadows
  2. very tight radii — 3px dominant (never pill-soft)
  3. CornerLines — small crosshair ticks at box corners (blueprint motif)
  4. paper-grain texture overlay (light 6%, dark 6.25%)
  5. warm neutral palette; pure white/black reserved for "loud" emphasis
  6. display type: semi-extended grotesque, -0.025em tracking; eyebrow labels
     uppercase with wide positive tracking
  7. monospace for numerals/metadata

Fonts: Hex uses Cinetype / PP Formula (commercial). Closest free stand-ins —
Inter (UI), Archivo semi-extended (display), IBM Plex Mono (in Hex's own stack).

Switch theme:  AXIS_THEME=dark  (default "light")
Usage:         import theme; theme.apply()
"""
import os
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# ---------------------------------------------------------------- brand (theme-independent)
BRAND_COLORS = {
    "black": "#01011b", "white": "#ffffff", "obsidian": "#14141C",
    "roseQuartz": "#F5C0C0", "amethyst": "#A477B2", "minsk": "#473982",
    "eggplant": "#31263B", "jade": "#5CB198", "citrine": "#CDA849",
    "opal": "#FBF9F9", "violetTopaz": "#5F509D", "cement": "#717A94",
}

# ---------------------------------------------------------------- exact hex.tech themes
THEMES = {
    "light": {
        "canvas":        "#fcf8f8",   # bg300
        "canvas_alt":    "#f9f1f1",   # bg400 — sidebar / blush band
        "surface":       "#FFFCFC",   # fg100 — cards sit slightly lighter than canvas
        "surface_hover": "#f8f5f6",   # fg200
        "surface_deep":  "#f4f1f2",   # fg300 — menus/popovers
        "border_soft":   "#e9e5e8",   # grid300
        "border":        "#e4e0e3",   # grid400
        "border_strong": "#cbc6cb",   # grid600
        "tick":          "#afa9b1",   # grid700 — corner marks
        "text":          "#31263B",   # fontColorDEFAULT (eggplant)
        "muted":         "#89828d",   # fontColorMUTED
        "loud":          "#14141C",   # fontColorLOUD (obsidian)
        "grain_opacity": "0.06",
        "grain_size":    "256px",
        "scroll":        "#cbc6cb",
        "scroll_hover":  "#afa9b1",
    },
    "dark": {
        "canvas":        "#0f0f15",   # bg300
        "canvas_alt":    "#14141c",   # bg500 — sidebar
        "surface":       "#111118",   # bg400
        "surface_hover": "#201d25",   # fg300
        "surface_deep":  "#252128",   # fg400
        "border_soft":   "#2b252c",   # grid300
        "border":        "#312a31",   # grid400
        "border_strong": "#4c3e44",   # grid600
        "tick":          "#99797d",
        "text":          "#F5C0C0",   # roseQuartz
        "muted":         "#99797d",
        "loud":          "#ffffff",
        "grain_opacity": "0.0625",
        "grain_size":    "200px",
        "scroll":        "#4c3e44",
        "scroll_hover":  "#6a565b",
    },
}

MODE = os.getenv("AXIS_THEME", "light").lower()
if MODE not in THEMES:
    MODE = "light"

# active palette = theme tokens + brand + geometry
T = {
    **BRAND_COLORS,
    **THEMES[MODE],
    "r": "3px", "r_md": "4px", "r_lg": "6px",
    "sans": "'Inter', 'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif",
    "display": "'Archivo', 'Inter', ui-sans-serif, system-ui, sans-serif",
    "mono": "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
}

# semantic sentiment — jade/citrine/cement are Hex's own; the negative is derived
# to harmonise with the warm palette (Hex publishes no semantic red).
SENT_COLORS = {
    "positive": T["jade"],                                  # #5CB198
    "negative": "#C4544F" if MODE == "light" else "#D65C5C",
    "neutral":  T["cement"],                                # #717A94
    "mixed":    "#B8912F" if MODE == "light" else T["citrine"],
}
DIVERGING = [SENT_COLORS["negative"], T["cement"], T["jade"]]
DIVERGING_R = [T["jade"], T["cement"], SENT_COLORS["negative"]]

# chart colorway — Hex's named brand colours, ordered
COLORWAY = [T["amethyst"], T["jade"], T["citrine"], T["cement"], T["violetTopaz"],
            T["minsk"], SENT_COLORS["negative"], "#7F8FA8", "#B9899B", T["eggplant"]]

FONT_URL = ("https://fonts.googleapis.com/css2?"
            "family=Archivo:wdth,wght@100..125,400..700"
            "&family=IBM+Plex+Mono:wght@400;500"
            "&family=Inter:wght@400;500;600;700&display=swap")

GRAIN = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E"
         "%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' "
         "stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)' "
         "opacity='0.5'/%3E%3C/svg%3E\")")


# ---------------------------------------------------------------- plotly
def install_plotly_template():
    tpl = go.layout.Template()
    axis = dict(
        gridcolor=T["border_soft"], zerolinecolor=T["border"], linecolor=T["border"],
        tickfont=dict(size=10, color=T["muted"], family=T["mono"]),
        title=dict(font=dict(size=11, color=T["muted"], family=T["sans"])),
    )
    tpl.layout = go.Layout(
        colorway=COLORWAY,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=T["sans"], size=12, color=T["muted"]),
        title=dict(font=dict(family=T["display"], size=13, color=T["text"]), x=0, xanchor="left"),
        margin=dict(t=18, b=16, l=16, r=16),
        xaxis=axis, yaxis=axis,
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11, color=T["muted"]),
                    orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    title=dict(text="")),
        hoverlabel=dict(bgcolor=T["surface"], bordercolor=T["border_strong"],
                        font=dict(family=T["mono"], size=11, color=T["text"])),
        colorscale=dict(diverging=[[0, SENT_COLORS["negative"]], [0.5, T["cement"]], [1, T["jade"]]]),
    )
    pio.templates["hex"] = tpl
    pio.templates.default = "hex"


# ---------------------------------------------------------------- css
def _css() -> str:
    t = T
    neg = SENT_COLORS["negative"]
    neg_rgb = "196,84,79" if MODE == "light" else "214,92,92"
    return f"""
<style>
@import url('{FONT_URL}');

:root {{
  --canvas:{t['canvas']}; --canvas-alt:{t['canvas_alt']}; --surface:{t['surface']};
  --surface-hover:{t['surface_hover']}; --surface-deep:{t['surface_deep']};
  --border-soft:{t['border_soft']}; --border:{t['border']}; --border-strong:{t['border_strong']};
  --tick:{t['tick']}; --text:{t['text']}; --muted:{t['muted']}; --loud:{t['loud']};
  --amethyst:{t['amethyst']}; --jade:{t['jade']}; --citrine:{t['citrine']};
  --minsk:{t['minsk']}; --violetTopaz:{t['violetTopaz']}; --cement:{t['cement']};
  --r:{t['r']}; --r-md:{t['r_md']};
}}

/* ---------- canvas + paper grain ---------- */
html, body, [data-testid="stAppViewContainer"] {{
  background:var(--canvas); color:var(--text);
  font-family:{t['sans']}; font-size:14px; -webkit-font-smoothing:antialiased;
}}
[data-testid="stAppViewContainer"]::before {{
  content:""; position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image:{GRAIN}; background-size:{t['grain_size']} {t['grain_size']};
  opacity:{t['grain_opacity']};
}}
[data-testid="stAppViewContainer"] > * {{ position:relative; z-index:1; }}
[data-testid="stHeader"] {{ background:transparent; border-bottom:1px solid var(--border-soft); }}
[data-testid="stDecoration"] {{ display:none; }}
.block-container {{ padding-top:2rem; padding-bottom:3rem; max-width:1680px; }}

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] {{ background:var(--canvas-alt); border-right:1px solid var(--border); }}
[data-testid="stSidebar"] .block-container {{ padding-top:1.3rem; }}
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{
  font-family:{t['mono']} !important; font-size:10px !important; font-weight:500 !important;
  letter-spacing:.14em; text-transform:uppercase; color:var(--muted) !important;
  margin:1.3rem 0 .5rem 0;
}}

/* ---------- type ---------- */
h1, h2, h3 {{ font-family:{t['display']} !important; font-variation-settings:'wdth' 112; }}
h1 {{ font-size:1.6rem !important; font-weight:600 !important; letter-spacing:-.025em; color:var(--loud) !important; }}
h2 {{ font-size:1.05rem !important; font-weight:600 !important; letter-spacing:-.02em; color:var(--text) !important; }}
h3 {{ font-size:.92rem !important; font-weight:600 !important; letter-spacing:-.015em; color:var(--text) !important; }}
p, li, span, div {{ color:var(--text); }}
[data-testid="stCaptionContainer"], .stCaption, small {{ color:var(--muted) !important; font-size:12px !important; }}
a {{ color:var(--minsk) !important; }}
hr, [data-testid="stDivider"] {{ border-color:var(--border-soft) !important; }}
code, kbd, pre {{ font-family:{t['mono']} !important; font-size:12px !important; }}

/* ---------- metric tiles + CornerLines ---------- */
[data-testid="stMetric"] {{
  position:relative; background:var(--surface); border:1px solid var(--border);
  border-radius:var(--r); padding:15px 16px 13px 16px;
  transition:border-color .15s ease, background .15s ease;
}}
[data-testid="stMetric"]:hover {{ border-color:var(--border-strong); background:var(--surface-hover); }}
[data-testid="stMetric"]::before, [data-testid="stMetric"]::after {{
  content:""; position:absolute; width:5px; height:5px; pointer-events:none; opacity:.9;
}}
[data-testid="stMetric"]::before {{ top:-1px; left:-1px; border-top:1px solid var(--tick); border-left:1px solid var(--tick); }}
[data-testid="stMetric"]::after  {{ bottom:-1px; right:-1px; border-bottom:1px solid var(--tick); border-right:1px solid var(--tick); }}
[data-testid="stMetricLabel"] p {{
  font-family:{t['mono']} !important; font-size:10px !important; font-weight:500 !important;
  letter-spacing:.12em; text-transform:uppercase; color:var(--muted) !important;
}}
[data-testid="stMetricValue"] {{
  font-family:{t['display']} !important; font-variation-settings:'wdth' 108;
  font-size:1.75rem !important; font-weight:600 !important; color:var(--loud) !important;
  font-variant-numeric:tabular-nums; letter-spacing:-.03em; line-height:1.15;
}}
[data-testid="stMetricDelta"] {{ font-family:{t['mono']} !important; font-size:11px !important; }}

/* ---------- tabs ---------- */
.stTabs [data-baseweb="tab-list"] {{ gap:0; border-bottom:1px solid var(--border); background:transparent; }}
.stTabs [data-baseweb="tab"] {{
  height:33px; padding:0 13px; background:transparent; border:none; border-radius:0;
  color:var(--muted); font-size:12.5px; font-weight:500;
}}
.stTabs [data-baseweb="tab"]:hover {{ color:var(--text); background:var(--surface-hover); }}
.stTabs [aria-selected="true"] {{ color:var(--loud) !important; background:var(--surface) !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ background:var(--amethyst); height:1.5px; }}
.stTabs [data-baseweb="tab-border"] {{ background:transparent; }}

/* ---------- buttons ---------- */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] button {{
  border-radius:var(--r); border:1px solid var(--border-strong); background:var(--surface);
  color:var(--text); font-family:{t['sans']}; font-size:12.5px; font-weight:500;
  padding:.38rem .85rem; transition:all .14s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
  border-color:var(--muted); background:var(--surface-hover); color:var(--loud);
}}
.stButton > button[kind="primary"] {{ background:var(--minsk); border-color:var(--minsk); color:#fff; }}
.stButton > button[kind="primary"]:hover {{ background:var(--violetTopaz); border-color:var(--violetTopaz); }}
.stButton > button:focus {{ outline:2px solid rgba(164,119,178,.5) !important; outline-offset:2px; box-shadow:none !important; }}

/* ---------- inputs ---------- */
[data-baseweb="select"] > div, [data-baseweb="input"] > div,
.stTextInput input, .stNumberInput input, .stTextArea textarea {{
  background:var(--surface) !important; border-color:var(--border-strong) !important;
  border-radius:var(--r) !important; color:var(--text) !important; font-size:12.5px !important;
}}
[data-baseweb="select"] > div:hover, [data-baseweb="input"] > div:hover {{ border-color:var(--muted) !important; }}
[data-baseweb="menu"] {{ background:var(--surface) !important; border:1px solid var(--border); }}
[data-baseweb="menu"] li {{ font-size:12.5px; color:var(--text); }}
[data-baseweb="tag"] {{
  background:rgba(164,119,178,.14) !important; border:1px solid rgba(164,119,178,.4) !important;
  color:var(--minsk) !important; border-radius:2px !important; font-size:11.5px !important;
}}
.stCheckbox p, .stRadio p, label p {{ font-size:12.5px !important; color:var(--muted) !important; }}
[data-testid="stWidgetLabel"] p {{
  font-family:{t['mono']} !important; font-size:10px !important; letter-spacing:.1em;
  text-transform:uppercase; color:var(--muted) !important;
}}

/* ---------- dataframes ---------- */
[data-testid="stDataFrame"], [data-testid="stTable"] {{
  border:1px solid var(--border); border-radius:var(--r); overflow:hidden; background:var(--surface);
}}
[data-testid="stDataFrame"] * {{ font-size:12px; }}
[data-testid="stDataFrame"] [role="columnheader"] {{
  font-family:{t['mono']} !important; font-size:10px !important; letter-spacing:.08em;
  text-transform:uppercase; color:var(--muted) !important;
}}

/* ---------- expander / alerts ---------- */
[data-testid="stExpander"] {{ border:1px solid var(--border); border-radius:var(--r); background:var(--surface); }}
[data-testid="stExpander"] summary {{ font-size:12.5px; font-weight:500; color:var(--text); }}
[data-testid="stAlert"] {{
  border-radius:var(--r); border:1px solid var(--border-strong); background:var(--surface);
  font-size:12.5px; color:var(--text);
}}

/* ---------- charts ---------- */
[data-testid="stPlotlyChart"] {{
  position:relative; background:var(--surface); border:1px solid var(--border);
  border-radius:var(--r); padding:10px 8px 4px 8px;
}}
[data-testid="stPlotlyChart"]::before {{
  content:""; position:absolute; top:-1px; left:-1px; width:5px; height:5px;
  border-top:1px solid var(--tick); border-left:1px solid var(--tick); pointer-events:none;
}}

/* ---------- live badge + ticker ---------- */
.live-badge {{
  display:inline-flex; align-items:center; gap:6px; font-family:{t['mono']};
  font-size:10px; font-weight:500; letter-spacing:.16em; text-transform:uppercase;
  color:{neg}; background:rgba({neg_rgb},.09); border:1px solid rgba({neg_rgb},.3);
  border-radius:var(--r); padding:3px 9px;
}}
.live-dot {{
  height:5px; width:5px; background:{neg}; border-radius:50%; display:inline-block;
  box-shadow:0 0 0 0 rgba({neg_rgb},.6); animation:pulse 1.8s infinite;
}}
@keyframes pulse {{
  0%{{box-shadow:0 0 0 0 rgba({neg_rgb},.5)}}
  70%{{box-shadow:0 0 0 6px rgba({neg_rgb},0)}}
  100%{{box-shadow:0 0 0 0 rgba({neg_rgb},0)}}
}}
.ticker {{
  overflow:hidden; white-space:nowrap; background:var(--surface);
  border:1px solid var(--border); border-radius:var(--r); padding:8px 0; margin:10px 0 4px 0;
}}
.ticker-track {{ display:inline-block; padding-left:100%; animation:scroll 65s linear infinite; }}
.ticker:hover .ticker-track {{ animation-play-state:paused; }}
@keyframes scroll {{ 0%{{transform:translateX(0)}} 100%{{transform:translateX(-100%)}} }}
.tk {{ margin:0 22px; font-size:12px; color:var(--text); }}
.tk-src {{ color:var(--muted); font-family:{t['mono']}; font-size:10px; letter-spacing:.08em; text-transform:uppercase; }}
.clock {{ font-family:{t['mono']}; font-variant-numeric:tabular-nums; color:var(--muted); font-size:11px; letter-spacing:.04em; }}

/* ---------- scrollbars ---------- */
::-webkit-scrollbar {{ width:8px; height:8px; }}
::-webkit-scrollbar-track {{ background:transparent; }}
::-webkit-scrollbar-thumb {{ background:{t['scroll']}; border-radius:2px; }}
::-webkit-scrollbar-thumb:hover {{ background:{t['scroll_hover']}; }}
</style>
"""


def _patch_plotly_chart():
    """Streamlit's built-in chart theme overrides our template (it forces its own
    fonts/colors). Default `theme=None` and assert ours on the figure — so the Hex
    look wins across all ~20 call sites without touching them."""
    if getattr(st, "_hex_patched", False):
        return
    _orig = st.plotly_chart

    def _themed(fig, *a, **kw):
        kw.setdefault("theme", None)
        try:
            fig.update_layout(
                font=dict(family=T["sans"], color=T["muted"], size=12),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                title_font=dict(family=T["display"], color=T["text"], size=13),
                legend_font=dict(family=T["sans"], color=T["muted"], size=11),
            )
        except Exception:
            pass
        return _orig(fig, *a, **kw)

    st.plotly_chart = _themed
    st._hex_patched = True


def strip_emoji(label: str) -> str:
    """Hex's UI is emoji-free. Clean a display label, keep the original as the key."""
    out = "".join(ch for ch in label if ord(ch) < 0x2190)
    return out.strip(" ·-—") or label


def apply():
    """Call once, right after st.set_page_config()."""
    install_plotly_template()
    _patch_plotly_chart()
    st.markdown(_css(), unsafe_allow_html=True)
