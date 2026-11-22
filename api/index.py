"""FastAPI app (Vercel Python function). Routes are under /api."""
from __future__ import annotations

import io
import json
from functools import lru_cache
import os
import sys
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.analysis import Settings, ValidationError, analyse  # noqa: E402
from engine.readout import llm_readout, rule_based  # noqa: E402
from engine.sample import build as build_sample  # noqa: E402

app = FastAPI(title="Promo Lift Analyzer API")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_readout_cache: dict[str, dict] = {}


def _margin(m: float | None) -> float:
    m = 0.40 if m is None else m
    if not 0.05 <= m <= 0.9:
        raise HTTPException(400, "margin_pct must be between 0.05 and 0.9")
    return round(m, 2)


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": bool(os.getenv("OPENROUTER_API_KEY"))}


@lru_cache(maxsize=1)
def _sample_df() -> pd.DataFrame:
    return build_sample()  # deterministic (seeded) synthetic dataset


@lru_cache(maxsize=8)
def _sample_result(m: float) -> dict:
    return analyse(_sample_df(), Settings(margin_pct=m))


@app.get("/api/sample")
def sample(margin_pct: float | None = None):
    result = _sample_result(_margin(margin_pct))
    return {**result, "readout": rule_based(result), "dataset": "sample"}


@app.get("/api/sample.csv")
def sample_csv():
    return Response(_sample_df().to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample_promo_data.csv"})


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), margin_pct: float = Form(0.40)):
    m = _margin(margin_pct)
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large for the demo (8 MB max).")
    try:
        df = pd.read_excel(io.BytesIO(raw)) if file.filename.lower().endswith((".xlsx", ".xls")) \
            else pd.read_csv(io.BytesIO(raw))
    except Exception as ex:
        raise HTTPException(400, f"Could not read the file: {ex}")
    try:
        result = analyse(df, Settings(margin_pct=m))
    except ValidationError as ex:
        return JSONResponse(status_code=422, content={"errors": ex.errors})
    return {**result, "readout": rule_based(result), "dataset": file.filename}


class ReadoutRequest(BaseModel):
    result: dict
    cache_key: str | None = None


@app.post("/api/readout")
def readout(req: ReadoutRequest):
    if len(json.dumps(req.result)) > 2_000_000:
        raise HTTPException(413, "Result too large")
    key = req.cache_key
    if key and key in _readout_cache:
        return _readout_cache[key]
    out = llm_readout(req.result)
    if key and out.get("source") == "llm":
        _readout_cache[key] = out
    return out
