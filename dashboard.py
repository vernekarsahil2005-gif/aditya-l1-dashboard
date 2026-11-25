import base64
import datetime
import io
import mimetypes
import os
import time
import altair as alt
import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="ADITYA-L1 | Space Weather Command Center",
    page_icon="☀️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- SESSION STATE INITIALIZATION ---
if "view" not in st.session_state:
    st.session_state["view"] = "landing"
if "active_module" not in st.session_state:
    st.session_state["active_module"] = "🛰️ Live Command Center"
if "replay_dt" not in st.session_state:
    st.session_state["replay_dt"] = datetime.datetime(2024, 5, 15, 8, 20, 0)
if "threshold" not in st.session_state:
    st.session_state["threshold"] = 0.10
if "auto_play" not in st.session_state:
    st.session_state["auto_play"] = False
if "play_step_mins" not in st.session_state:
    st.session_state["play_step_mins"] = 2

# ==============================================================================
# SUBTLE PHOTOREALISTIC SPACE BACKGROUND CONFIGURATION
# ==============================================================================
OVERLAY_OPACITY = 0.50      # 0.45 - 0.60: higher = darker/calmer, lower = image shows more
BG_IMAGE_OPACITY = 0.90     # Photo strength (was 0.36, which double-dimmed it with the overlay)
BG_MOTION_SECONDS = 100     # 80 - 120s slow Ken Burns zoom & pan cycle
ORBIT_SECONDS = 54          # 45 - 60s seamless elliptical satellite orbit
SAT_SIZE_PX = 90            # Subtle satellite width in pixels
SAT_OPACITY = 0.75          # Satellite visibility (0.5 - 0.9)
SHOW_ORBIT_LINE = True      # Faint elliptical orbit guide line

BACKGROUNDS = {
    "landing":   "assets/assetslanding.jpg",
    "module_01": "assets/assetsmodule1.jpg",
    "module_02": "assets/assetsmodule2.jpg",
    "module_03": "assets/assetsmodule3.jpg",
    "module_04": "assets/assetsmodule4.jpg",
    "module_05": "assets/assetsmodule2.jpg",
}

# Put your satellite image (on a pure black background) at frontend/assets/satellite.png
# If the file is missing, the satellite is simply skipped (nothing breaks).
SATELLITE_ASSET = "assets/satellite.png"

MODULE_BG_MAP = {
    "🛰️ Live Command Center": "module_01",
    "🔬 +10m Ground-Truth Verifier": "module_02",
    "🧠 AI Explainability & Physics": "module_03",
    "📂 External CSV Inference Lab": "module_04",
    "📘 Mission Architecture & Audit": "module_05",
}


BG_DEBUG = True   # Shows a red warning on the page if an image can't be found. Set False when everything works.
# If your images live somewhere unusual, paste the full folder path here, e.g. r"D:\\MyProject\\frontend\\assets"
ASSETS_DIR = ""


def _asset_search_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    cwd = os.getcwd()
    dirs = []
    if ASSETS_DIR and os.path.isdir(ASSETS_DIR):
        dirs.append(os.path.normpath(ASSETS_DIR))
    for root in (here, os.path.join(here, "frontend"), parent, os.path.join(parent, "frontend"), cwd,
                 os.path.join(cwd, "frontend")):
        for sub in ("assets", "", "static"):
            d = os.path.normpath(os.path.join(root, sub))
            if os.path.isdir(d) and d not in dirs:
                dirs.append(d)
    return dirs


def _resolve_asset_path(rel_path: str):
    """Finds the image wherever it is: tries 'assetslanding.jpg' AND 'landing.jpg',
    any of .jpg/.jpeg/.png/.webp, in assets/, frontend/assets/, next to this file, etc."""
    if not rel_path:
        return None
    base = os.path.splitext(os.path.basename(rel_path.replace("\\", "/")))[0].lower()
    stems = {base}
    if base.startswith("assets"):
        stems.add(base[len("assets"):])
    else:
        stems.add("assets" + base)
    exts = (".jpg", ".jpeg", ".png", ".webp")

    def _strip_exts(name: str) -> str:
        # "assetslanding.jpg.png" -> "assetslanding"  (handles double extensions)
        name = name.lower()
        changed = True
        while changed:
            changed = False
            for e in exts:
                if name.endswith(e):
                    name = name[: -len(e)]
                    changed = True
        return name

    for d in _asset_search_dirs():
        try:
            for fname in os.listdir(d):
                if fname.lower().endswith(exts) and _strip_exts(fname) in stems:
                    return os.path.join(d, fname)
        except OSError:
            continue
    return None


@st.cache_data(show_spinner=False)
def _encode_image(path: str, max_width: int, _mtime: float):
    try:
        from PIL import Image
        with Image.open(path) as img:
            if img.width > max_width:
                ratio = max_width / float(img.width)
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            w, h = img.size
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}", w, h
    except Exception:
        mime, _ = mimetypes.guess_type(path)
        if not mime:
            mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime};base64,{b64}", 16, 9


def _load_image(rel_path: str, max_width: int = 1600):
    """Returns (data_uri, width, height, path) or None. 'Not found' is never cached."""
    path = _resolve_asset_path(rel_path)
    if not path:
        return None
    uri, w, h = _encode_image(path, max_width, os.path.getmtime(path))
    return uri, w, h, path


def set_background(page_key: str) -> None:
    rel_path = BACKGROUNDS.get(page_key, BACKGROUNDS["landing"])
    bg = _load_image(rel_path, max_width=1600)
    sat = _load_image(SATELLITE_ASSET, max_width=360)

    if BG_DEBUG:
        if bg:
            st.caption(f"BACKGROUND v3 OK | {page_key} -> {os.path.basename(bg[3])} ({len(bg[0]) // 1024} KB)")
        else:
            st.error(
                f"Background image not found for '{page_key}' (looked for {os.path.basename(rel_path)}). "
                f"Searched: {_asset_search_dirs()}"
            )
            for _d in _asset_search_dirs()[:2]:
                try:
                    _imgs = [f for f in os.listdir(_d) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
                except OSError:
                    _imgs = []
                st.caption(f"Images found in {_d}: {_imgs if _imgs else 'NONE'}")

    ov_top = max(0.0, min(OVERLAY_OPACITY - 0.05, 0.90))
    ov_mid = max(0.0, min(OVERLAY_OPACITY, 0.92))
    ov_bot = max(0.0, min(OVERLAY_OPACITY + 0.07, 0.95))

    photo_css = (
        f'background-image:url("{bg[0]}");background-size:cover;background-position:center;'
        if bg
        else "background:radial-gradient(ellipse at 75% 30%,#0c1f3d 0%,#040b18 60%,#02050d 100%);"
    )
    if sat:
        sat_h = max(1, int(SAT_SIZE_PX * sat[2] / float(sat[1])))
        sat_css = (
            f'#space-bg-root .sb-sat-img{{width:{SAT_SIZE_PX}px;height:{sat_h}px;display:block;'
            f'background-image:url("{sat[0]}");background-size:contain;background-repeat:no-repeat;'
            f'mix-blend-mode:screen;opacity:{SAT_OPACITY};'
            f'filter:brightness(0.9) drop-shadow(0 0 10px rgba(125,211,252,0.25));'
            f'animation:sbOrbitY {ORBIT_SECONDS}s ease-in-out infinite alternate,'
            f'sbSatTilt {int(ORBIT_SECONDS * 0.7)}s ease-in-out infinite alternate;}}'
        )
        sat_html = '<div class="sb-sat-orbit"><div class="sb-sat-carrier"><div class="sb-sat-img"></div></div></div>'
    else:
        sat_css, sat_html = "", ""
    orbit_line_html = '<div class="sb-orbit-ring"></div>' if SHOW_ORBIT_LINE else ""

    html_block = (
        f'<style>'
        # make Streamlit's own backgrounds transparent so the fixed layer behind is visible
        f'html,body,.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"],[data-testid="stHeader"],'
        f'[data-testid="stDecoration"],.main,.main .block-container{{'
        f'background:transparent !important;background-color:transparent !important;}}'
        f'.stApp{{isolation:isolate;}}'
        f'[data-testid="stHeader"]{{z-index:6 !important;}}'
        f'section[data-testid="stSidebar"]{{'
        f'background-color:rgba(4,10,24,0.78) !important;'
        f'backdrop-filter:blur(14px) !important;-webkit-backdrop-filter:blur(14px) !important;'
        f'border-right:1px solid rgba(56,189,248,0.22) !important;}}'
        f'.hero-shell,.mod-card,.mission-bar,.kpi-card,.glass-panel,[data-testid="stMetric"]{{'
        f'background-color:rgba(6,14,30,0.58) !important;'
        f'backdrop-filter:blur(12px) saturate(115%) !important;'
        f'-webkit-backdrop-filter:blur(12px) saturate(115%) !important;}}'
        f'.stApp h1,.stApp h2,.stApp h3,.stApp .hero-headline,.stApp .hero-sub{{text-shadow:0 1px 8px rgba(2,6,18,0.85);}}'
        # fixed layer sits BEHIND all content (z-index:-1 inside the isolated .stApp)
        f'#space-bg-root{{position:fixed;inset:0;width:100vw;height:100vh;z-index:-1;'
        f'pointer-events:none;overflow:hidden;background:#030816;}}'
        f'#space-bg-root .sb-photo-img{{position:absolute;inset:-6%;{photo_css}'
        f'opacity:{BG_IMAGE_OPACITY};filter:saturate(0.95) brightness(0.92);'
        f'will-change:transform;animation:sbKenBurns {BG_MOTION_SECONDS}s ease-in-out infinite alternate;}}'
        f'#space-bg-root .sb-stars{{position:absolute;inset:-10%;width:120%;height:120%;opacity:0.22;'
        f'background-image:'
        f'radial-gradient(1.1px 1.1px at 14% 22%,#e2e8f0,transparent),'
        f'radial-gradient(1px 1px at 31% 68%,#bae6fd,transparent),'
        f'radial-gradient(1.2px 1.2px at 52% 18%,#ffffff,transparent),'
        f'radial-gradient(1px 1px at 67% 81%,#cbd5e1,transparent),'
        f'radial-gradient(1.1px 1.1px at 84% 39%,#e0f2fe,transparent),'
        f'radial-gradient(1px 1px at 22% 88%,#ffffff,transparent),'
        f'radial-gradient(1.2px 1.2px at 74% 12%,#bae6fd,transparent);'
        f'background-size:480px 480px;'
        f'animation:sbStarDrift {int(BG_MOTION_SECONDS * 1.4)}s linear infinite alternate;}}'
        f'#space-bg-root .sb-scrim{{position:absolute;inset:0;'
        f'background:linear-gradient(110deg,'
        f'rgba(3,9,23,{ov_bot}) 0%,rgba(4,12,28,{ov_mid}) 52%,rgba(3,9,23,{ov_top}) 100%);}}'
        f'#space-bg-root .sb-orbit-ring{{position:fixed;top:50%;left:54%;width:78vw;height:46vh;'
        f'margin-left:-39vw;margin-top:-23vh;border:1px solid rgba(125,211,252,0.10);'
        f'border-radius:50%;transform:rotate(-11deg);}}'
        f'#space-bg-root .sb-sat-orbit{{position:fixed;top:50%;left:54%;width:0;height:0;'
        f'transform:rotate(-11deg);}}'
        f'#space-bg-root .sb-sat-carrier{{position:absolute;top:0;left:0;will-change:transform;'
        f'animation:sbOrbitX {ORBIT_SECONDS}s ease-in-out infinite alternate;}}'
        f'{sat_css}'
        f'@keyframes sbKenBurns{{'
        f'0%{{transform:scale(1.00) translate3d(-1.1%,-0.7%,0);}}'
        f'100%{{transform:scale(1.08) translate3d(1.1%,0.9%,0);}}}}'
        f'@keyframes sbStarDrift{{'
        f'0%{{transform:translate3d(0px,0px,0);}}'
        f'100%{{transform:translate3d(-36px,-22px,0);}}}}'
        f'@keyframes sbOrbitX{{'
        f'0%{{transform:translateX(-39vw);}}'
        f'100%{{transform:translateX(39vw);}}}}'
        f'@keyframes sbOrbitY{{'
        f'0%{{transform:translateY(-23vh);}}'
        f'50%{{transform:translateY(23vh);}}'
        f'100%{{transform:translateY(-23vh);}}}}'
        f'@keyframes sbSatTilt{{'
        f'0%{{rotate:-8deg;}}'
        f'100%{{rotate:10deg;}}}}'
        f'@media (prefers-reduced-motion:reduce){{'
        f'#space-bg-root *{{animation:none !important;transition:none !important;}}}}'
        f'</style>'
        f'<div id="space-bg-root" aria-hidden="true">'
        f'<div class="sb-photo-img"></div><div class="sb-stars"></div><div class="sb-scrim"></div>'
        f'{orbit_line_html}{sat_html}</div>'
    )
    st.markdown(html_block, unsafe_allow_html=True)


# --- BASE THEME & COMPONENT STYLES ---
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600;700&family=Orbitron:wght@600;700;800&display=swap');

.stApp { color: #e2e8f0; font-family: 'Inter', sans-serif; }
.orbit-title { font-family: 'Orbitron', sans-serif; letter-spacing: 2px; }
.mono { font-family: 'JetBrains Mono', monospace; }

.hero-shell {
    border: 1px solid rgba(56, 189, 248, 0.35);
    border-radius: 16px;
    padding: 40px 44px;
    box-shadow: 0 0 50px rgba(56, 189, 248, 0.14);
    margin-top: 12px;
    margin-bottom: 22px;
}
.hero-eyebrow {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 3px; color: #38bdf8;
    text-transform: uppercase; margin-bottom: 12px;
}
.hero-headline {
    font-family: 'Orbitron', sans-serif;
    font-size: 40px; font-weight: 800; line-height: 1.15;
    color: #f8fafc; margin-bottom: 16px;
}
.hero-sub {
    font-size: 16px; line-height: 1.65; color: #cbd5e1;
    max-width: 800px; margin-bottom: 12px;
}
.mod-card {
    border: 1px solid rgba(148, 163, 184, 0.20);
    border-top: 2px solid #38bdf8;
    border-radius: 12px; padding: 20px; min-height: 150px; margin-bottom: 12px;
}
.mission-bar {
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-left: 4px solid #38bdf8;
    border-radius: 10px; padding: 16px 22px; margin-bottom: 18px;
    display: flex; justify-content: space-between; align-items: center;
}
.kpi-card {
    border: 1px solid rgba(148, 163, 184, 0.20);
    border-top: 3px solid #38bdf8;
    border-radius: 10px; padding: 18px 20px;
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.50); min-height: 142px;
}
.kpi-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; letter-spacing: 1.4px; color: #94a3b8; text-transform: uppercase;
}
.kpi-value {
    font-family: 'Orbitron', sans-serif; font-size: 19px; font-weight: 700; margin: 8px 0 6px 0;
}
.glass-panel {
    border: 1px solid rgba(148, 163, 184, 0.20);
    border-radius: 12px; padding: 20px 22px; margin-bottom: 14px;
}
.pill-green {
    display: inline-block; background: rgba(34, 197, 94, 0.16); color: #4ade80;
    border: 1px solid rgba(34, 197, 94, 0.55); padding: 4px 12px; border-radius: 999px;
    font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 600;
}
.pill-red {
    display: inline-block; background: rgba(239, 68, 68, 0.16); color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.55); padding: 4px 12px; border-radius: 999px;
    font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 600;
}
.pill-amber {
    display: inline-block; background: rgba(245, 158, 11, 0.16); color: #fbbf24;
    border: 1px solid rgba(245, 158, 11, 0.55); padding: 4px 12px; border-radius: 999px;
    font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 600;
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(90deg, #0284c7 0%, #2563eb 100%);
    color: #ffffff; border: 1px solid #38bdf8;
    font-family: 'Orbitron', sans-serif; letter-spacing: 1px;
    padding: 0.65rem 1.4rem; border-radius: 8px;
    box-shadow: 0 0 20px rgba(56, 189, 248, 0.30);
}
</style>""", unsafe_allow_html=True)

if st.session_state["view"] == "landing":
    st.markdown(
        "<style>section[data-testid='stSidebar'] { display: none; }</style>",
        unsafe_allow_html=True,
    )


# --- API HELPERS ---
@st.cache_data(ttl=15)
def fetch_health():
    try:
        r = requests.get(f"{API_BASE}/api/health", timeout=3)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def fetch_prediction(ts_str: str, threshold: float):
    r = requests.get(
        f"{API_BASE}/api/predict/realtime",
        params={"timestamp": ts_str, "threshold": threshold},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def fetch_replay(start_str: str, end_str: str):
    r = requests.get(
        f"{API_BASE}/api/telemetry/replay",
        params={"start_time": start_str, "end_time": end_str},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def fetch_verification(ts_str: str, threshold: float):
    r = requests.get(
        f"{API_BASE}/api/verify/future",
        params={"timestamp": ts_str, "threshold": threshold},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()


def go_to_module(module_name: str):
    st.session_state["auto_play"] = False
    st.session_state["view"] = "console"
    st.session_state["active_module"] = module_name
    st.rerun()


# --- REUSABLE UI COMPONENTS ---
def render_kpi_cards(pred: dict):
    is_active = pred["nowcast"]["is_active_flare"]
    is_alert = pred["forecast_15m"]["alert"]
    h_mins = pred["forecast_15m"].get("horizon_mins", 10)
    prob_pct = pred["forecast_15m"]["probability_pct"]
    thresh_pct = round(pred.get("threshold_used", 0.10) * 100, 1)
    ttp = pred.get("mins_to_peak")

    now_color = "#ef4444" if is_active else ("#f59e0b" if is_alert else "#22c55e")
    fc_color = "#ef4444" if is_alert else "#22c55e"
    imp_color = "#ef4444" if is_alert else "#22c55e"

    ttp_badge = f" • Est. Peak in ~{ttp}m" if ttp is not None else f" • Cutoff: {thresh_pct}%"

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""
        <div class="kpi-card" style="border-top-color:{now_color};">
            <div class="kpi-label">CURRENT SOLAR STATE (NOWCAST T)</div>
            <div class="kpi-value" style="color:{now_color};">{pred['nowcast']['status']}</div>
            <div class="mono" style="color:#cbd5e1; font-size:12px;">
                SoLEXS SXR: <b>{pred['telemetry']['sxr_flux']}</b> &nbsp;|&nbsp;
                HEL1OS HXR: <b>{pred['telemetry']['hxr_mid']}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with c2:
        st.markdown(f"""
        <div class="kpi-card" style="border-top-color:{fc_color};">
            <div class="kpi-label">PREDICTIVE FORECAST (+{h_mins} MIN HORIZON)</div>
            <div class="kpi-value" style="color:{fc_color};">{pred['forecast_15m']['label']}</div>
            <div class="mono" style="color:#38bdf8; font-size:12px;">
                Prob: <b>{prob_pct}%</b>{ttp_badge}<br>
                Proj SXR: <b>{pred['forecast_15m']['projected_sxr']}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with c3:
        st.markdown(f"""
        <div class="kpi-card" style="border-top-color:{imp_color};">
            <div class="kpi-label">OPERATIONAL IMPACT ASSESSMENT</div>
            <div style="display:flex; justify-content:space-between; margin-top:12px;">
                <div>
                    <div class="kpi-label" style="font-size:10px;">NOAA RADIO</div>
                    <div class="mono" style="font-size:16px; font-weight:700; color:{imp_color}; margin-top:4px;">
                        {pred['impact']['radio_blackout']}
                    </div>
                </div>
                <div>
                    <div class="kpi-label" style="font-size:10px;">SAT DRAG / SEU</div>
                    <div class="mono" style="font-size:16px; font-weight:700; color:{imp_color}; margin-top:4px;">
                        {pred['impact']['satellite_risk']}
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.progress(min(max(prob_pct / 100.0, 0.0), 1.0))


def render_explainability_chart(explain_dict: dict, height: int = 260):
    exp_df = pd.DataFrame({
        "Feature": list(explain_dict.keys()),
        "Impact": list(explain_dict.values()),
    })
    exp_df["Direction"] = exp_df["Impact"].apply(
        lambda v: "Increases Flare Risk" if v >= 0 else "Reduces Flare Risk"
    )
    bar = (
        alt.Chart(exp_df)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("Impact:Q", title="XGBoost TreeSHAP Log-Odds Contribution"),
            y=alt.Y("Feature:N", sort="-x", title=None),
            color=alt.Color(
                "Direction:N",
                scale=alt.Scale(
                    domain=["Increases Flare Risk", "Reduces Flare Risk"],
                    range=["#ef4444", "#22c55e"],
                ),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            tooltip=["Feature:N", "Impact:Q", "Direction:N"],
        )
        .properties(height=height)
    )
    st.altair_chart(bar, use_container_width=True)


def render_dual_band_charts(replay: dict, ts_str: str, height: int = 330):
    if not replay.get("timestamps"):
        st.warning("No telemetry points available in this window.")
        return

    chart_df = pd.DataFrame({
        "Time": pd.to_datetime(replay["timestamps"]),
        "SoLEXS SXR": replay["sxr_flux"],
        "HEL1OS HXR": replay["hxr_mid"],
    })
    now_rule = (
        alt.Chart(pd.DataFrame({"Time": [pd.to_datetime(ts_str)]}))
        .mark_rule(color="#ef4444", strokeDash=[6, 4], strokeWidth=2)
        .encode(x="Time:T")
    )

    t1, t2 = st.tabs([
        "SoLEXS Soft X-Ray (2–22 keV Thermal)",
        "HEL1OS Hard X-Ray (8–150 keV Impulsive)",
    ])
    with t1:
        c_sxr = (
            alt.Chart(chart_df)
            .mark_area(
                line={"color": "#00d4ff", "strokeWidth": 2.2},
                color=alt.Gradient(
                    gradient="linear",
                    stops=[
                        alt.GradientStop(color="rgba(0,212,255,0.45)", offset=0),
                        alt.GradientStop(color="rgba(0,212,255,0.02)", offset=1),
                    ],
                    x1=1, x2=1, y1=1, y2=0,
                ),
            )
            .encode(
                x=alt.X("Time:T", title="UTC Time"),
                y=alt.Y("SoLEXS SXR:Q", title="SXR Flux (counts/s)"),
                tooltip=["Time:T", "SoLEXS SXR:Q"],
            )
            .properties(height=height)
        )
        st.altair_chart((c_sxr + now_rule).interactive(), use_container_width=True)

    with t2:
        c_hxr = (
            alt.Chart(chart_df)
            .mark_area(
                line={"color": "#f59e0b", "strokeWidth": 2.2},
                color=alt.Gradient(
                    gradient="linear",
                    stops=[
                        alt.GradientStop(color="rgba(245,158,11,0.45)", offset=0),
                        alt.GradientStop(color="rgba(245,158,11,0.02)", offset=1),
                    ],
                    x1=1, x2=1, y1=1, y2=0,
                ),
            )
            .encode(
                x=alt.X("Time:T", title="UTC Time"),
                y=alt.Y("HEL1OS HXR:Q", title="HXR Flux (counts/s)"),
                tooltip=["Time:T", "HEL1OS HXR:Q"],
            )
            .properties(height=height)
        )
        st.altair_chart((c_hxr + now_rule).interactive(), use_container_width=True)


health = fetch_health()
horizon_mins = health.get("horizon_mins", 10) if health else 10
metrics = health.get("metrics", {}) if health else {}

# ==============================================================================
# VIEW 1: NASA / SPACEX MISSION LAUNCHPAD (LANDING PAGE)
# ==============================================================================
if st.session_state["view"] == "landing":
    set_background("landing")

    status_pill = (
        '<span class="pill-green">● TELEMETRY ENGINE ONLINE (XGBOOST v2 • 45F)</span>'
        if health
        else '<span class="pill-red">● BACKEND OFFLINE — START PORT 8000</span>'
    )
    rows_txt = f"{health['rows']:,}" if health else "8-DAY ARCHIVE"
    tss_txt = f"{metrics.get('test_tss', 0.8188):.4f}"
    rec_txt = f"{metrics.get('test_recall', 1.0) * 100:.1f}%"

    st.markdown(f"""
        <div class="hero-shell">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                <div class="hero-eyebrow">ISRO ADITYA-L1 • LAGRANGE POINT L1 (1.5M KM)</div>
                {status_pill}
            </div>
            <div class="hero-headline">
                SOLAR FLARE EARLY-WARNING<br>MISSION CONTROL
            </div>
            <div class="hero-sub">
                Drift-free dual-payload X-ray fusion combining <b>SoLEXS (Soft X-Ray)</b> and
                <b>HEL1OS (Hard X-Ray)</b> across <b>45 engineered causal features</b> to deliver a
                verified <b>{horizon_mins}-minute predictive warning</b> before peak coronal eruption.
            </div>
        </div>
    """, unsafe_allow_html=True)

    cta1, cta2, cta3 = st.columns([1.25, 1.25, 2.5])
    with cta1:
        if st.button("🚀 LAUNCH MISSION CONTROL", type="primary", use_container_width=True):
            go_to_module("🛰️ Live Command Center")
    with cta2:
        if st.button(f"🔬 OPEN +{horizon_mins}M VERIFIER", use_container_width=True):
            go_to_module("🔬 +10m Ground-Truth Verifier")
    with cta3:
        with st.expander("🎤 30-Second Judge Pitch Brief"):
            st.write(
                f"Aditya-L1 at L1 streams SoLEXS thermal SXR and HEL1OS non-thermal HXR telemetry. "
                f"Our drift-free 45-feature XGBoost model achieves **{tss_txt} Test TSS** and **{rec_txt} Recall** "
                f"with a **{horizon_mins}-minute lead time**, beating our CNN-BiLSTM baseline (`0.6612` TSS)."
            )

    st.markdown("<br>", unsafe_allow_html=True)

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("TEST TSS (OUT-OF-SAMPLE)", tss_txt, "Beats CNN-BiLSTM (0.6612)")
    s2.metric("TEST FLARE RECALL", rec_txt, f"ROC-AUC: {metrics.get('test_roc_auc', 0.9742):.4f}")
    s3.metric("PREDICTIVE LEAD TIME", f"{horizon_mins} MIN", f"Δt = {horizon_mins * 60} Seconds")
    s4.metric("FUSED TELEMETRY", f"{rows_txt} ROWS", "8-Day Multi-Event Dataset")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 🧭 SELECT A MISSION SUBSYSTEM")

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown("""
            <div class="mod-card">
                <div class="kpi-label" style="color:#38bdf8;">MODULE 01</div>
                <h4 style="margin:6px 0; color:#f8fafc;">🛰️ Live Command Center</h4>
                <p style="font-size:13px; color:#94a3b8;">
                    No-peeking replay with live auto-play, dual-band light curves, and presets.
                </p>
            </div>
        """, unsafe_allow_html=True)
        if st.button("Open Command Center →", key="btn_m1", use_container_width=True):
            go_to_module("🛰️ Live Command Center")

    with m2:
        st.markdown(f"""
            <div class="mod-card" style="border-top-color:#22c55e;">
                <div class="kpi-label" style="color:#4ade80;">MODULE 02</div>
                <h4 style="margin:6px 0; color:#f8fafc;">🔬 +{horizon_mins}m Verifier</h4>
                <p style="font-size:13px; color:#94a3b8;">
                    Lock forecast at T and reveal actual observed T+{horizon_mins}m ground truth.
                </p>
            </div>
        """, unsafe_allow_html=True)
        if st.button("Open Verifier →", key="btn_m2", use_container_width=True):
            go_to_module("🔬 +10m Ground-Truth Verifier")

    with m3:
        st.markdown("""
            <div class="mod-card" style="border-top-color:#f59e0b;">
                <div class="kpi-label" style="color:#fbbf24;">MODULE 03</div>
                <h4 style="margin:6px 0; color:#f8fafc;">🧠 AI Explainability</h4>
                <p style="font-size:13px; color:#94a3b8;">
                    Inspect live XGBoost TreeSHAP top feature drivers and Neupert physics signals.
                </p>
            </div>
        """, unsafe_allow_html=True)
        if st.button("Open Explainability →", key="btn_m3", use_container_width=True):
            go_to_module("🧠 AI Explainability & Physics")

    with m4:
        st.markdown("""
            <div class="mod-card" style="border-top-color:#a855f7;">
                <div class="kpi-label" style="color:#c084fc;">MODULE 04</div>
                <h4 style="margin:6px 0; color:#f8fafc;">📂 CSV Inference Lab</h4>
                <p style="font-size:13px; color:#94a3b8;">
                    Upload calm, pre-flare, or active eruption CSVs for live 45-feature inference.
                </p>
            </div>
        """, unsafe_allow_html=True)
        if st.button("Open CSV Lab →", key="btn_m4", use_container_width=True):
            go_to_module("📂 External CSV Inference Lab")

    st.stop()


# ==============================================================================
# VIEW 2: MAIN MISSION CONTROL CONSOLE (SIDEBAR + 5 INTERACTIVE DASHBOARDS)
# ==============================================================================
MODULES = [
    "🛰️ Live Command Center",
    "🔬 +10m Ground-Truth Verifier",
    "🧠 AI Explainability & Physics",
    "📂 External CSV Inference Lab",
    "📘 Mission Architecture & Audit",
]

st.sidebar.markdown(
    "<div class='orbit-title' style='font-size:16px; color:#38bdf8; margin-bottom:4px;'>☀️ ADITYA-L1 OPS</div>",
    unsafe_allow_html=True,
)
st.sidebar.caption("ISRO Space Weather Early-Warning Suite (v2)")

if st.sidebar.button("← Return to Launchpad", use_container_width=True):
    st.session_state["auto_play"] = False
    st.session_state["view"] = "landing"
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 🧭 Mission Subsystems")
if st.session_state["active_module"] not in MODULES:
    st.session_state["active_module"] = MODULES[0]

active_module = st.sidebar.radio(
    "Select Dashboard",
    MODULES,
    index=MODULES.index(st.session_state["active_module"]),
    label_visibility="collapsed",
)
st.session_state["active_module"] = active_module
set_background(MODULE_BG_MAP.get(active_module, "module_01"))

min_dt = (
    datetime.datetime.strptime(health["min_timestamp"], "%Y-%m-%d %H:%M:%S")
    if (health and health.get("min_timestamp"))
    else datetime.datetime(2024, 5, 15, 7, 19, 0)
)
max_dt = (
    datetime.datetime.strptime(health["max_timestamp"], "%Y-%m-%d %H:%M:%S")
    if (health and health.get("max_timestamp"))
    else datetime.datetime(2024, 5, 15, 11, 18, 0)
)

if not (min_dt <= st.session_state["replay_dt"] <= max_dt):
    st.session_state["replay_dt"] = min_dt + (max_dt - min_dt) // 2

if active_module in (
    "🛰️ Live Command Center",
    "🔬 +10m Ground-Truth Verifier",
    "🧠 AI Explainability & Physics",
):
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⚡ Quick Event Presets")
    p1, p2, p3 = st.sidebar.columns(3)
    if p1.button("Quiet", use_container_width=True):
        st.session_state["auto_play"] = False
        st.session_state["replay_dt"] = datetime.datetime(2024, 5, 15, 7, 48, 0)
    if p2.button("Pre-Flare", use_container_width=True):
        st.session_state["auto_play"] = False
        st.session_state["replay_dt"] = datetime.datetime(2024, 5, 15, 8, 20, 0)
    if p3.button("Eruption", use_container_width=True):
        st.session_state["auto_play"] = False
        st.session_state["replay_dt"] = datetime.datetime(2024, 5, 15, 8, 36, 0)

    selected_dt = st.sidebar.slider(
        "Mission Clock T (UTC)",
        min_value=min_dt,
        max_value=max_dt,
        value=st.session_state["replay_dt"],
        step=datetime.timedelta(minutes=1),
        format="YYYY-MM-DD HH:mm",
    )
    st.session_state["replay_dt"] = selected_dt

    st.sidebar.markdown("### ▶️ Telemetry Playback")
    pb1, pb2 = st.sidebar.columns(2)
    play_label = "⏸️ Pause" if st.session_state["auto_play"] else "▶️ Auto-Play"
    if pb1.button(play_label, use_container_width=True):
        st.session_state["auto_play"] = not st.session_state["auto_play"]
        st.rerun()
    if pb2.button("⏭️ Step +2m", use_container_width=True):
        nxt = st.session_state["replay_dt"] + datetime.timedelta(minutes=2)
        st.session_state["replay_dt"] = min(nxt, max_dt)
        st.rerun()

threshold = st.sidebar.slider(
    "Alert Decision Threshold",
    0.05,
    0.90,
    st.session_state["threshold"],
    0.05,
)
st.session_state["threshold"] = threshold

ts_str = st.session_state["replay_dt"].strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------------------
# MODULE 1: LIVE COMMAND CENTER
# ------------------------------------------------------------------------------
if active_module == "🛰️ Live Command Center":
    lookback_mins = st.select_slider(
        "Replay Window Lookback",
        options=[15, 30, 45, 90, 180],
        value=45,
        format_func=lambda m: f"Last {m} mins",
    )
    start_dt = st.session_state["replay_dt"] - datetime.timedelta(minutes=lookback_mins)
    try:
        pred = fetch_prediction(ts_str, threshold)
        replay = fetch_replay(start_dt.strftime("%Y-%m-%d %H:%M:%S"), ts_str)
    except Exception as e:
        st.error(f"Cannot reach FastAPI backend at {API_BASE}. ({e})")
        st.stop()

    st.markdown(f"""
        <div class="mission-bar">
            <div>
                <div class="kpi-label" style="color:#38bdf8;">MODULE 01 • STRICT NO-PEEKING REPLAY (XGBOOST v2)</div>
                <div class="orbit-title" style="font-size:22px; color:#f8fafc; margin-top:2px;">
                    🛰️ LIVE SPACE WEATHER COMMAND CENTER
                </div>
            </div>
            <div style="text-align:right;">
                <span class="pill-green">● ENGINE ACTIVE</span>
                <div class="mono" style="font-size:12px; color:#94a3b8; margin-top:6px;">
                    NOW (T): <b style="color:#f8fafc;">{pred['timestamp']}</b> &nbsp;|&nbsp;
                    TARGET (T+{horizon_mins}m): <b style="color:#38bdf8;">{pred['target_timestamp']}</b>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    render_kpi_cards(pred)
    st.markdown("<br>", unsafe_allow_html=True)

    left_col, right_col = st.columns([2.2, 1.0])
    with left_col:
        st.markdown("#### 📈 DUAL-BAND SOLAR LIGHT CURVES (NO-PEEKING CUTOFF AT T)")
        render_dual_band_charts(replay, ts_str, height=340)

    with right_col:
        st.markdown("#### 📡 SATELLITE & COMMS MATRIX")
        sat_mode = "SAFE-MODE ADVISED" if pred["forecast_15m"]["alert"] else "NOMINAL OPERATIONS"
        st.markdown(f"""
            <div class="glass-panel">
                <div class="mono" style="font-size:13px; line-height:2.0;">
                    📻 <b>HF Aviation / Comms:</b> {pred['impact']['comms_status']}<br>
                    🛰️ <b>LEO/GEO Posture:</b> {sat_mode}<br>
                    ⏱️ <b>Forecast Lead Time:</b> {horizon_mins} Minutes ({horizon_mins * 60}s)
                </div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown("#### ⚡ LIVE NEUPERT SIGNALS")
        sig_df = pd.DataFrame(
            list(pred["feature_signals"].items()),
            columns=["Indicator", "Value"],
        ).set_index("Indicator")
        st.dataframe(sig_df, use_container_width=True)

    st.markdown("---")
    if st.button(f"🔬 Verify This Prediction at T + {horizon_mins} Min →", type="primary"):
        go_to_module("🔬 +10m Ground-Truth Verifier")


# ------------------------------------------------------------------------------
# MODULE 2: +10 MIN GROUND-TRUTH VERIFIER
# ------------------------------------------------------------------------------
elif active_module == "🔬 +10m Ground-Truth Verifier":
    st.markdown(f"""
        <div class="mission-bar" style="border-left-color:#22c55e;">
            <div>
                <div class="kpi-label" style="color:#4ade80;">MODULE 02 • JUDGE VERIFICATION CONSOLE</div>
                <div class="orbit-title" style="font-size:22px; color:#f8fafc; margin-top:2px;">
                    🔬 +{horizon_mins} MINUTE GROUND-TRUTH VERIFIER
                </div>
            </div>
            <span class="pill-green">● AUDIT MODE READY</span>
        </div>
    """, unsafe_allow_html=True)

    reveal = st.toggle(
        f"🔓 Reveal Actual T + {horizon_mins} Min Aditya-L1 Ground Truth",
        value=True,
    )

    start_dt = st.session_state["replay_dt"] - datetime.timedelta(minutes=30)
    end_dt = (
        st.session_state["replay_dt"] + datetime.timedelta(minutes=horizon_mins)
        if reveal
        else st.session_state["replay_dt"]
    )

    try:
        pred = fetch_prediction(ts_str, threshold)
        replay = fetch_replay(
            start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        )
        vdata = fetch_verification(ts_str, threshold) if reveal else None
    except Exception as e:
        st.error(f"Backend verification error: {e}")
        st.stop()

    if reveal and vdata:
        v_code = vdata["verdict_code"]
        pill = "pill-green" if v_code in ("HIT", "NOMINAL") else "pill-amber"
        st.markdown(f"""
            <div class="glass-panel" style="border-left:4px solid #22c55e;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div class="kpi-label">FORECAST AT {vdata['base_time']} → VERIFIED AT {vdata['verified_time']}</div>
                        <div class="orbit-title" style="font-size:18px; color:#f8fafc; margin-top:4px;">
                            OUTCOME COMPARISON SUMMARY
                        </div>
                    </div>
                    <span class="{pill}">{vdata['verdict']}</span>
                </div>
            </div>
        """, unsafe_allow_html=True)

        v1, v2, v3, v4 = st.columns(4)
        v1.metric(
            "Model Forecast at T",
            "FLARE RISK" if vdata["predicted_alert"] else "NOMINAL",
            f"{vdata['predicted_prob_pct']}% probability",
        )
        v2.metric(f"Actual State (T+{horizon_mins}m)", vdata["actual_state"])
        v3.metric("Projected SXR Flux", f"{vdata['projected_sxr']}")
        v4.metric(
            f"Actual Peak SXR ({horizon_mins}m)",
            f"{vdata['peak_sxr_in_window']}",
            f"Exact T+{horizon_mins}m: {vdata['actual_sxr']}",
        )
    else:
        st.info(
            f"🔒 Future telemetry past **{pred['timestamp']}** is hidden. "
            f"Model locks in **{pred['forecast_15m']['label']} ({pred['forecast_15m']['probability_pct']}%)** "
            f"for **{pred['target_timestamp']}**. Toggle ON above to verify."
        )

    st.markdown("#### 📈 FORECAST HORIZON TIMELINE (RED LINE = NOWCAST INSTANT T)")
    render_dual_band_charts(replay, ts_str, height=360)


# ------------------------------------------------------------------------------
# MODULE 3: AI EXPLAINABILITY & PHYSICS
# ------------------------------------------------------------------------------
elif active_module == "🧠 AI Explainability & Physics":
    try:
        pred = fetch_prediction(ts_str, threshold)
    except Exception as e:
        st.error(f"Backend error: {e}")
        st.stop()

    st.markdown(f"""
        <div class="mission-bar" style="border-left-color:#f59e0b;">
            <div>
                <div class="kpi-label" style="color:#fbbf24;">MODULE 03 • TRANSPARENT AI DIAGNOSTICS</div>
                <div class="orbit-title" style="font-size:22px; color:#f8fafc; margin-top:2px;">
                    🧠 XGBOOST TREESHAP & NEUPERT PHYSICS
                </div>
            </div>
            <div class="mono" style="font-size:12px; color:#cbd5e1;">
                EVALUATED AT: <b>{pred['timestamp']}</b>
            </div>
        </div>
    """, unsafe_allow_html=True)

    render_kpi_cards(pred)
    st.markdown("<br>", unsafe_allow_html=True)

    e1, e2 = st.columns([1.45, 1.0])
    with e1:
        st.markdown("#### 🎯 TOP 8 TREESHAP FEATURE DRIVERS (OUT OF 45)")
        st.caption("Red bars push the model toward a flare alert; green bars suppress alert probability.")
        render_explainability_chart(pred["explainability"], height=330)

    with e2:
        st.markdown("#### ⚡ ENGINEERED NEUPERT DERIVATIVES")
        sig_df = pd.DataFrame(
            list(pred["feature_signals"].items()),
            columns=["Physics Indicator", "Live Value"],
        ).set_index("Physics Indicator")
        st.dataframe(sig_df, use_container_width=True)

        st.markdown("""
            <div class="glass-panel">
                <div class="kpi-label" style="color:#fbbf24; margin-bottom:6px;">DRIFT-FREE PHYSICS</div>
                <div style="font-size:13px; color:#cbd5e1; line-height:1.6;">
                    By replacing raw SXR flux with a <b>causal 1-hour background-relative ratio (sxr_rel_bg)</b>
                    and log-transforming HEL1OS Hard X-ray bands, the model stays immune to solar-cycle baseline drift.
                </div>
            </div>
        """, unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# MODULE 4: EXTERNAL CSV INFERENCE LAB
# ------------------------------------------------------------------------------
elif active_module == "📂 External CSV Inference Lab":
    st.markdown("""
        <div class="mission-bar" style="border-left-color:#a855f7;">
            <div>
                <div class="kpi-label" style="color:#c084fc;">MODULE 04 • LIVE EXTERNAL INFERENCE</div>
                <div class="orbit-title" style="font-size:22px; color:#f8fafc; margin-top:2px;">
                    📂 TELEMETRY CSV INFERENCE LAB
                </div>
            </div>
            <span class="pill-green">● XGBOOST 45F READY</span>
        </div>
    """, unsafe_allow_html=True)

    col_up, col_dl = st.columns([2.2, 1.0])
    with col_dl:
        st.markdown("#### ⬇️ 1-Click Test Dataset")
        st.caption("Download a real 15-minute Aditya-L1 slice or test with calm / eruption CSVs.")
        try:
            sample_csv = requests.get(f"{API_BASE}/api/sample-csv", timeout=5).json()["csv_text"]
            st.download_button(
                "Download Sample Telemetry CSV",
                data=sample_csv,
                file_name="aditya_l1_v2_sample.csv",
                mime="text/csv",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"Sample CSV unavailable: {e}")

    with col_up:
        uploaded = st.file_uploader(
            "Upload Telemetry CSV (9 raw Aditya-L1 channels — 45 features are engineered automatically)",
            type=["csv"],
        )

    if uploaded is not None:
        csv_text = uploaded.getvalue().decode("utf-8")
        resp = requests.post(
            f"{API_BASE}/api/predict/upload",
            json={"csv_text": csv_text, "threshold": threshold},
            timeout=15,
        )
        if resp.status_code == 200:
            pred = resp.json()
            st.success(
                f"Live 45-feature inference completed across **{pred['rows_processed']} rows** "
                f"(evaluating latest instant: `{pred['timestamp']}`)."
            )
            render_kpi_cards(pred)
            st.markdown("<br>", unsafe_allow_html=True)

            c_left, c_right = st.columns([1.4, 1.0])
            with c_left:
                st.markdown("#### 🧠 TOP SHAP DRIVERS FOR UPLOADED TELEMETRY")
                render_explainability_chart(pred["explainability"], height=280)
            with c_right:
                st.markdown("#### ⚡ UPLOADED TELEMETRY PREVIEW")
                preview_df = pd.read_csv(io.StringIO(csv_text)).tail(6)
                st.dataframe(preview_df, use_container_width=True)
        else:
            st.error(f"Upload inference failed: {resp.json().get('detail', resp.text)}")


# ------------------------------------------------------------------------------
# MODULE 5: MISSION ARCHITECTURE & MODEL AUDIT
# ------------------------------------------------------------------------------
elif active_module == "📘 Mission Architecture & Audit":
    st.markdown("""
        <div class="mission-bar">
            <div>
                <div class="kpi-label" style="color:#38bdf8;">MODULE 05 • SCIENTIFIC METHODOLOGY</div>
                <div class="orbit-title" style="font-size:22px; color:#f8fafc; margin-top:2px;">
                    📘 MISSION ARCHITECTURE & VALIDATION AUDIT
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("TEST TSS", f"{metrics.get('test_tss', 0.8188):.4f}", f"Val TSS: {metrics.get('val_tss', 0.7807):.4f}")
    a2.metric("TEST ROC-AUC", f"{metrics.get('test_roc_auc', 0.9742):.4f}", "XGBoost vs 0.85 CNN-BiLSTM")
    a3.metric("TEST RECALL (POD)", f"{metrics.get('test_recall', 1.0) * 100:.1f}%", f"Specificity: {metrics.get('test_specificity', 0.8188) * 100:.2f}%")
    a4.metric("FEATURE PIPELINE", "45 FEATURES", f"+{horizon_mins} Min Causal Horizon")

    st.markdown("<br>", unsafe_allow_html=True)
    b1, b2 = st.columns(2)
    with b1:
        st.markdown("""
            <div class="glass-panel">
                <div class="kpi-label" style="color:#38bdf8; margin-bottom:8px;">01. DRIFT-FREE DUAL-PAYLOAD PHYSICS</div>
                <p style="font-size:14px; color:#cbd5e1; line-height:1.65;">
                    ISRO's <b>Aditya-L1</b> orbits Lagrange Point L1 (1.5M km sunward). Our v2 engine expands
                    9 raw <b>SoLEXS + HEL1OS</b> channels into <b>45 causal rolling, lag, and derivative features</b>
                    using a 1-hour background-relative SXR ratio—outperforming deep CNN-BiLSTM benchmarks
                    (<b>0.8188 vs 0.6612 Test TSS</b>) with sub-millisecond inference.
                </p>
            </div>
        """, unsafe_allow_html=True)

    with b2:
        st.markdown(f"""
            <div class="glass-panel">
                <div class="kpi-label" style="color:#4ade80; margin-bottom:8px;">02. SCIENTIFICALLY RIGOROUS VALIDATION</div>
                <p style="font-size:14px; color:#cbd5e1; line-height:1.65;">
                    Trained and validated across an <b>8-day multi-event Aditya-L1 telemetry archive</b> with
                    strict temporal isolation. Judges can verify predictions interactively via
                    <b>No-Peeking Replay</b>, <b>One-Click +{horizon_mins}m Ground Truth</b>, and
                    <b>Live External CSV Inference</b>.
                </p>
            </div>
        """, unsafe_allow_html=True)


# --- AUTO-PLAY LOOP HANDLER ---
if (
    st.session_state["auto_play"]
    and st.session_state["view"] == "console"
    and active_module in (
        "🛰️ Live Command Center",
        "🔬 +10m Ground-Truth Verifier",
        "🧠 AI Explainability & Physics",
    )
):
    nxt_dt = st.session_state["replay_dt"] + datetime.timedelta(minutes=st.session_state["play_step_mins"])
    if nxt_dt > max_dt:
        st.session_state["auto_play"] = False
    else:
        time.sleep(1.2)
        st.session_state["replay_dt"] = nxt_dt
        st.rerun()