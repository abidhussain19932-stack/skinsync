"""SkinSync — FastAPI app: YouCam Skin Analysis + Skin Tone + Makeup VTO, orchestrated by an LLM agent."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, catalog, demo, imaging
from .config import settings
from .youcam import YouCamClient, YouCamError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
SAMPLE = os.path.join(STATIC, "img", "sample_selfie.jpg")
SAMPLE_AFTER = os.path.join(STATIC, "img", "sample_after_dewy.jpg")

app = FastAPI(title="SkinSync", version="1.0.0",
              description="AI skin coach + beauty shop powered by YouCam AI APIs")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# In-memory session store (prototype). Selfies are never written to disk and expire with the process.
SESSIONS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
MAX_SESSIONS = 200


def _session(sid: str) -> dict:
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, "Session expired — please analyze again.")
    return s


def _save(s: dict) -> str:
    sid = uuid.uuid4().hex[:16]
    SESSIONS[sid] = s
    while len(SESSIONS) > MAX_SESSIONS:
        SESSIONS.popitem(last=False)
    return sid


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/health")
def health():
    return {"ok": True, "demo_mode": settings.demo_mode, "youcam_base_url": settings.youcam_base_url,
            "llm_model": settings.llm_model if settings.llm_enabled else None,
            "youcam_features": ["skin-analysis", "skin-tone-analysis", "makeup-vto"]}


@app.get("/api/catalog")
def get_catalog():
    return catalog.load_catalog()


@app.post("/api/analyze")
async def analyze(file: UploadFile | None = File(None), sample: bool = Form(False)):
    if sample or file is None:
        with open(SAMPLE, "rb") as f:
            original = f.read()
        sample = True
    else:
        original = await file.read()
        if len(original) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"Image too large (max {settings.max_upload_mb} MB)")
        try:
            imaging.load(original)
        except Exception:
            raise HTTPException(400, "That file doesn't look like a JPG or PNG photo.")
    t0 = time.time()
    skin_img = imaging.prep_for_skin(original)
    calls: list[dict] = []
    if settings.demo_mode:
        await asyncio.sleep(0.6)  # keep the UI's progress states visible
        analysis, tone = demo.skin_analysis(skin_img, sample), demo.skin_tone(skin_img, sample)
        calls = [{"method": "DEMO", "endpoint": "/s2s/v2.0/task/skin-analysis", "ms": 0, "note": "canned response"},
                 {"method": "DEMO", "endpoint": "/s2s/v2.0/task/skin-tone-analysis", "ms": 0, "note": "canned response"}]
    else:
        yc_a, yc_t = YouCamClient(), YouCamClient()
        try:
            analysis, tone = await asyncio.gather(yc_a.skin_analysis(skin_img), yc_t.skin_tone(skin_img))
        except YouCamError as e:
            return JSONResponse({"error": str(e), "code": e.code}, status_code=422)
        calls = yc_a.calls + yc_t.calls
    sid = _save({"original": original, "skin_img": skin_img, "sample": sample, "analysis": analysis, "tone": tone,
                 "created": time.time()})
    return {"session_id": sid, "mode": "demo" if settings.demo_mode else "live", "analysis": analysis, "tone": tone,
            "labels": catalog.CONCERN_LABELS, "youcam_calls": calls, "elapsed_ms": int((time.time() - t0) * 1000),
            "image_url": f"/api/image/{sid}/before"}


class PlanReq(BaseModel):
    session_id: str
    prefs: dict[str, Any] = {}


class RefineReq(BaseModel):
    session_id: str
    instruction: str


class TryOnReq(BaseModel):
    session_id: str
    look_id: str | None = None
    lip_id: str | None = None
    blush_id: str | None = None
    foundation_id: str | None = None


@app.post("/api/plan")
async def make_plan(req: PlanReq):
    s = _session(req.session_id)
    s["prefs"] = req.prefs
    s["plan"] = await agent.plan(s["analysis"], s["tone"], req.prefs)
    return s["plan"]


@app.post("/api/refine")
async def refine(req: RefineReq):
    s = _session(req.session_id)
    s["plan"] = await agent.plan(s["analysis"], s["tone"], s.get("prefs", {}), previous=s.get("plan"),
                                 instruction=req.instruction[:500])
    return s["plan"]


@app.post("/api/tryon")
async def tryon(req: TryOnReq):
    s = _session(req.session_id)
    look = (s.get("plan") or {}).get("look", {})
    look_id = req.look_id or look.get("look_id", "fresh-dewy")

    def sku(pid, fallback):
        p = catalog.get_product(pid) if pid else None
        return catalog.compact(p) if p else fallback

    fnd = sku(req.foundation_id, look.get("foundation"))
    lip = sku(req.lip_id, look.get("lip"))
    blush = sku(req.blush_id, look.get("blush"))
    effects = catalog.build_effects(look_id, fnd, lip, blush)
    t0 = time.time()
    calls: list[dict] = []
    if settings.demo_mode:
        await asyncio.sleep(0.8)
        if s["sample"]:
            with open(SAMPLE_AFTER, "rb") as f:
                s["after"] = f.read()
            note = "Demo mode: pre-rendered try-on of the sample selfie."
        else:
            s["after"] = imaging.demo_tryon(s["original"], lip and lip.get("shade_hex"), blush and blush.get("shade_hex"))
            note = "Demo mode: approximate preview. Add YOUCAM_API_KEY for real YouCam makeup try-on."
        calls = [{"method": "DEMO", "endpoint": "/s2s/v2.0/task/makeup-vto", "ms": 0, "note": "canned response"}]
    else:
        yc = YouCamClient()
        try:
            url = await yc.makeup_vto(imaging.prep_for_vto(s["original"]), effects)
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.get(url)
                r.raise_for_status()
                s["after"] = r.content
        except (YouCamError, httpx.HTTPError) as e:
            return JSONResponse({"error": str(e), "effects": effects}, status_code=422)
        calls, note = yc.calls, "Rendered by YouCam AI Makeup Virtual Try-On."
    s["after_v"] = s.get("after_v", 0) + 1
    return {"after_url": f"/api/image/{req.session_id}/after?v={s['after_v']}", "before_url": f"/api/image/{req.session_id}/before",
            "effects": effects, "note": note, "youcam_calls": calls, "look_id": look_id,
            "products": [p for p in (fnd, lip, blush) if p], "elapsed_ms": int((time.time() - t0) * 1000)}


@app.get("/api/image/{sid}/{kind}")
def image(sid: str, kind: str):
    s = _session(sid)
    data = s.get("original") if kind == "before" else s.get("after")
    if not data:
        raise HTTPException(404)
    if kind == "before":
        data = imaging.to_jpeg(imaging.fit(imaging.load(data), 1080, 1350))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon():
    return RedirectResponse("/static/img/favicon.svg")
