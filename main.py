import io
import json
import os
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Aditya-L1 Mission Control API (v2 XGBoost)", version="5.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "master_telemetry_v2.parquet")
MODEL_PATH = os.path.join(BASE_DIR, "models", "v2_xgb_45f.joblib")
META_PATH = os.path.join(BASE_DIR, "models", "v2_metadata.json")

RAW_FEATURES = [
    "sxr_flux", "hxr_low", "hxr_mid", "hxr_high", "hxr_missing",
    "hardness_ratio", "sxr_slope_60s", "hxr_slope_60s", "hxr_fluence_300s",
]
LOG_COLS = ["hxr_low", "hxr_mid", "hxr_high", "hxr_fluence_300s"]

telemetry_df = None
features_df = None
clf_model = None
metadata = {}


class CSVUploadRequest(BaseModel):
    csv_text: str
    threshold: float = 0.10


def engineer_features_drift_free(df_in: pd.DataFrame, engineered_features: list) -> pd.DataFrame:
    d = df_in[RAW_FEATURES].copy()

    # 1. Causal 1-hour background-relative SXR ratio (replaces raw sxr_flux)
    sxr_bg_1h = d["sxr_flux"].rolling(3600, min_periods=60).median().bfill()
    d["sxr_rel_bg"] = np.log1p(np.clip(d["sxr_flux"] / (sxr_bg_1h + 1.0), 0.0, None))
    d = d.drop(columns=["sxr_flux"])

    # 2. Log-transform heavy-tailed HXR & fluence channels
    for c in LOG_COLS:
        d[c] = np.log1p(np.clip(d[c].values, 0.0, None))

    # 3. Expand 9 base channels -> 45 causal features
    new_cols = {}
    for col in list(d.columns):
        s = d[col]
        new_cols[f"{col}_roll_mean_10"] = s.rolling(10, min_periods=1).mean()
        new_cols[f"{col}_roll_mean_30"] = s.rolling(30, min_periods=1).mean()
        new_cols[f"{col}_diff_5"] = s.diff(5).fillna(0.0)
        new_cols[f"{col}_lag_10"] = s.shift(10).fillna(0.0)

    feat_df = pd.concat([d, pd.DataFrame(new_cols, index=d.index)], axis=1)
    return feat_df[engineered_features].replace([np.inf, -np.inf], 0.0).fillna(0.0)


@app.on_event("startup")
def load_resources():
    global telemetry_df, features_df, clf_model, metadata
    if not os.path.exists(DATA_PATH):
        raise RuntimeError(f"Dataset not found at {DATA_PATH}")
    if not os.path.exists(MODEL_PATH):
        raise RuntimeError(f"Model not found at {MODEL_PATH}")
    if not os.path.exists(META_PATH):
        raise RuntimeError(f"Metadata not found at {META_PATH}")

    with open(META_PATH, "r") as f:
        metadata = json.load(f)

    df = pd.read_parquet(DATA_PATH)
    if "timestamp_utc" in df.columns:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        df.set_index("timestamp_utc", inplace=True)
    else:
        df.index = pd.to_datetime(df.index, utc=True)

    telemetry_df = df.sort_index()
    clf_model = joblib.load(MODEL_PATH)

    features_df = engineer_features_drift_free(
        telemetry_df, metadata["engineered_features"]
    )


def _get_idx_at(timestamp: str = None) -> int:
    if timestamp:
        target_ts = pd.to_datetime(timestamp, utc=True)
        return int(telemetry_df.index.get_indexer([target_ts], method="nearest")[0])
    return len(telemetry_df) - 1


def _get_top_shap_contribs(X_input: pd.DataFrame, top_k: int = 8) -> dict:
    try:
        booster = clf_model.get_booster()
        dmat = xgb.DMatrix(X_input, feature_names=list(X_input.columns))
        contribs = booster.predict(dmat, pred_contribs=True)[0][:-1]
        pairs = list(zip(X_input.columns, contribs))
        pairs.sort(key=lambda x: abs(x[1]), reverse=True)
        return {name: round(float(val), 4) for name, val in pairs[:top_k]}
    except Exception:
        return {col: 0.0 for col in list(X_input.columns)[:top_k]}


def _build_prediction_payload(
    raw_row: pd.DataFrame, X_input: pd.DataFrame, threshold: float, ts_now: pd.Timestamp
):
    probs = clf_model.predict_proba(X_input)[0]
    flare_prob = float(probs[1])
    is_alert = flare_prob >= threshold

    sxr_val = float(raw_row["sxr_flux"].iloc[0])
    hxr_val = float(raw_row["hxr_mid"].iloc[0])
    sxr_slope = float(raw_row["sxr_slope_60s"].iloc[0])
    horizon_mins = int(metadata.get("forecast_horizon_seconds", 600) // 60)

    mins_to_peak = None
    if "target_mins_to_peak" in raw_row.columns and pd.notna(raw_row["target_mins_to_peak"].iloc[0]):
        val = float(raw_row["target_mins_to_peak"].iloc[0])
        if 0.0 <= val <= 120.0:
            mins_to_peak = round(val, 1)

    projected_sxr = max(0.0, sxr_val + (sxr_slope * 3.0) + (flare_prob * 600.0))

    if sxr_val >= 5000.0:
        nowcast_label = "ACTIVE FLARE (M/X-CLASS)"
        radio_blackout = "R3 STRONG"
    elif is_alert:
        nowcast_label = "PRE-FLARE ELEVATED"
        radio_blackout = "R1-R2 WATCH"
    else:
        nowcast_label = "QUIET SUN (NOMINAL)"
        radio_blackout = "R0 NONE"

    ts_future = ts_now + pd.Timedelta(minutes=horizon_mins)

    return {
        "timestamp": ts_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "target_timestamp": ts_future.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "mins_to_peak": mins_to_peak,
        "threshold_used": round(float(threshold), 3),
        "telemetry": {
            "sxr_flux": round(sxr_val, 2),
            "hxr_mid": round(hxr_val, 2),
            "hardness_ratio": round(float(raw_row["hardness_ratio"].iloc[0]), 4),
        },
        "nowcast": {
            "status": nowcast_label,
            "is_active_flare": sxr_val >= 5000.0,
        },
        "forecast_15m": {
            "alert": is_alert,
            "label": "FLARE RISK DETECTED" if is_alert else "NOMINAL (LOW RISK)",
            "probability_pct": round(flare_prob * 100, 1),
            "projected_sxr": round(projected_sxr, 1),
            "horizon_mins": horizon_mins,
        },
        "impact": {
            "radio_blackout": radio_blackout,
            "satellite_risk": "HIGH (DRAG / SEU)" if is_alert else "LOW",
            "comms_status": "DEGRADED" if (is_alert or sxr_val >= 5000.0) else "NOMINAL",
        },
        "feature_signals": {
            "SXR Rel-Bg (Log)": round(float(X_input["sxr_rel_bg"].iloc[0]), 4),
            "SXR 60s Slope": round(sxr_slope, 3),
            "HXR 60s Slope": round(float(raw_row["hxr_slope_60s"].iloc[0]), 3),
            "Hardness Ratio": round(float(raw_row["hardness_ratio"].iloc[0]), 4),
            "HXR 300s Fluence": round(float(raw_row["hxr_fluence_300s"].iloc[0]), 2),
        },
        "explainability": _get_top_shap_contribs(X_input, top_k=8),
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "ACTIVE",
        "version": "5.1.0",
        "model_type": metadata.get("model_type", "xgboost_drift_free"),
        "input_dim": metadata.get("input_dim", 45),
        "horizon_mins": int(metadata.get("forecast_horizon_seconds", 600) // 60),
        "optimal_threshold": metadata.get("optimal_threshold", 0.10),
        "metrics": {
            "test_tss": metadata.get("test_tss", 0.8188),
            "val_tss": metadata.get("val_tss", 0.7807),
            "test_roc_auc": metadata.get("test_roc_auc", 0.9742),
            "test_recall": metadata.get("test_recall", 1.0),
            "test_specificity": metadata.get("test_specificity", 0.8188),
            "cnn_bilstm_test_tss": metadata.get("benchmark_cnn_bilstm", {}).get("test_tss", 0.6612),
        },
        "rows": len(telemetry_df) if telemetry_df is not None else 0,
        "min_timestamp": telemetry_df.index.min().strftime("%Y-%m-%d %H:%M:%S") if telemetry_df is not None else None,
        "max_timestamp": telemetry_df.index.max().strftime("%Y-%m-%d %H:%M:%S") if telemetry_df is not None else None,
    }


@app.get("/api/telemetry/replay")
def get_replay_slice(start_time: str, end_time: str, max_points: int = 600):
    if telemetry_df is None:
        raise HTTPException(status_code=500, detail="Telemetry not loaded.")
    try:
        start_ts = pd.to_datetime(start_time, utc=True)
        end_ts = pd.to_datetime(end_time, utc=True)
        slice_df = telemetry_df.loc[start_ts:end_ts]

        if slice_df.empty:
            return {"timestamps": [], "sxr_flux": [], "hxr_mid": []}

        if len(slice_df) > max_points:
            step = max(1, len(slice_df) // max_points)
            slice_df = slice_df.iloc[::step]

        return {
            "timestamps": slice_df.index.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "sxr_flux": slice_df["sxr_flux"].round(2).tolist(),
            "hxr_mid": slice_df["hxr_mid"].round(2).tolist(),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/predict/realtime")
def get_realtime_prediction(timestamp: str = None, threshold: float = 0.10):
    if telemetry_df is None or clf_model is None:
        raise HTTPException(status_code=500, detail="Backend not initialized.")
    idx = _get_idx_at(timestamp)
    raw_row = telemetry_df.iloc[[idx]]
    X_input = features_df.iloc[[idx]]
    return _build_prediction_payload(raw_row, X_input, threshold, raw_row.index[0])


@app.get("/api/verify/future")
def verify_future_outcome(timestamp: str, threshold: float = 0.10):
    if telemetry_df is None or clf_model is None:
        raise HTTPException(status_code=500, detail="Backend not initialized.")

    horizon_mins = int(metadata.get("forecast_horizon_seconds", 600) // 60)
    pred = get_realtime_prediction(timestamp=timestamp, threshold=threshold)

    idx_now = _get_idx_at(timestamp)
    raw_now = telemetry_df.iloc[[idx_now]]
    ts_now = raw_now.index[0]
    ts_future = ts_now + pd.Timedelta(minutes=horizon_mins)

    future_slice = telemetry_df.loc[ts_now:ts_future]
    idx_fut = _get_idx_at(ts_future.strftime("%Y-%m-%d %H:%M:%S"))
    actual_row = telemetry_df.iloc[[idx_fut]]

    actual_sxr = float(actual_row["sxr_flux"].iloc[0])
    actual_hxr = float(actual_row["hxr_mid"].iloc[0])
    peak_sxr_win = float(future_slice["sxr_flux"].max()) if not future_slice.empty else actual_sxr

    if "target_flare_class" in raw_now.columns:
        actual_elevated = int(raw_now["target_flare_class"].iloc[0]) == 1
    else:
        actual_elevated = (peak_sxr_win >= 1000.0) or (actual_sxr > pred["telemetry"]["sxr_flux"] * 1.15)

    pred_alert = pred["forecast_15m"]["alert"]

    if pred_alert and actual_elevated:
        verdict, verdict_code = "VERIFIED HIT (TRUE POSITIVE)", "HIT"
    elif (not pred_alert) and (not actual_elevated):
        verdict, verdict_code = "VERIFIED NOMINAL (TRUE NEGATIVE)", "NOMINAL"
    elif pred_alert and (not actual_elevated):
        verdict, verdict_code = "CAUTIONARY ALERT (FALSE ALARM)", "WARN"
    else:
        verdict, verdict_code = "MISSED SURGE (FALSE NEGATIVE)", "MISS"

    return {
        "base_time": pred["timestamp"],
        "verified_time": actual_row.index[0].strftime("%Y-%m-%d %H:%M:%S UTC"),
        "horizon_mins": horizon_mins,
        "predicted_alert": pred_alert,
        "predicted_prob_pct": pred["forecast_15m"]["probability_pct"],
        "projected_sxr": pred["forecast_15m"]["projected_sxr"],
        "actual_sxr": round(actual_sxr, 2),
        "actual_hxr": round(actual_hxr, 2),
        "peak_sxr_in_window": round(peak_sxr_win, 2),
        "actual_state": "ELEVATED / FLARE ACTIVITY" if actual_elevated else "QUIET / NOMINAL",
        "verdict": verdict,
        "verdict_code": verdict_code,
    }


@app.post("/api/predict/upload")
def predict_from_csv(payload: CSVUploadRequest):
    if clf_model is None:
        raise HTTPException(status_code=500, detail="Model not initialized.")
    try:
        df = pd.read_csv(io.StringIO(payload.csv_text))
        missing = [c for c in RAW_FEATURES if c not in df.columns]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required raw columns: {', '.join(missing)}",
            )
        if df.empty:
            raise HTTPException(status_code=400, detail="Uploaded CSV has no rows.")

        X_all = engineer_features_drift_free(df, metadata["engineered_features"])
        raw_row = df.tail(1)
        X_input = X_all.tail(1)

        if "timestamp_utc" in df.columns:
            ts_now = pd.to_datetime(raw_row["timestamp_utc"].iloc[0], utc=True)
        else:
            ts_now = pd.Timestamp.utcnow()

        result = _build_prediction_payload(raw_row, X_input, payload.threshold, ts_now)
        result["rows_processed"] = len(df)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV parsing error: {e}")


@app.get("/api/sample-csv")
def get_sample_csv(timestamp: str = "2024-05-15 08:20:00"):
    if telemetry_df is None:
        raise HTTPException(status_code=500, detail="Telemetry not loaded.")
    end_ts = pd.to_datetime(timestamp, utc=True)
    start_ts = end_ts - pd.Timedelta(minutes=15)
    sample = telemetry_df.loc[start_ts:end_ts][RAW_FEATURES].iloc[::5].reset_index()
    sample.rename(columns={sample.columns[0]: "timestamp_utc"}, inplace=True)
    return {"csv_text": sample.to_csv(index=False)}