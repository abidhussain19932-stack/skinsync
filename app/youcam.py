"""Thin async client for the YouCam (Perfect Corp) AI API, v2.

Docs: https://yce.perfectcorp.com/document/index.html  (India/global host docs: https://yce.makeupar.com/ai-api)

Flow for every feature (skin-analysis, skin-tone-analysis, makeup-vto):
  1. POST {base}/s2s/v2.0/file/{feature}      -> file_id + pre-signed PUT url
  2. PUT  <pre-signed url>  (raw image bytes, headers from step 1)
  3. POST {base}/s2s/v2.0/task/{feature}      -> task_id
  4. GET  {base}/s2s/v2.0/task/{feature}/{id} -> poll until task_status is "success" | "error"
Auth: header `Authorization: Bearer <YOUCAM_API_KEY>` (v2 has no separate auth call).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .config import settings

# All SD skin concerns (HD and SD cannot be mixed in one task; SD needs short side >= 480px).
SD_SKIN_ACTIONS = [
    "wrinkle", "pore", "texture", "acne", "oiliness", "radiance", "eye_bag", "age_spot",
    "dark_circle_v2", "droopy_upper_eyelid", "droopy_lower_eyelid", "firmness", "moisture", "redness",
]


class YouCamError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class YouCamClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key if api_key is not None else settings.youcam_api_key
        self.base = (base_url or settings.youcam_base_url).rstrip("/")
        self.calls: list[dict[str, Any]] = []  # lightweight trace for the UI

    # ---------------- low level ----------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _check(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except Exception:
            raise YouCamError(f"Non-JSON response ({resp.status_code}): {resp.text[:200]}", status=resp.status_code)
        if resp.status_code >= 400 or (isinstance(body, dict) and body.get("status", 200) >= 400):
            raise YouCamError(
                body.get("error") or body.get("error_message") or f"HTTP {resp.status_code}",
                code=body.get("error_code"),
                status=resp.status_code,
            )
        return body

    def _log(self, method: str, path: str, t0: float, note: str = "") -> None:
        self.calls.append({"method": method, "endpoint": path, "ms": int((time.time() - t0) * 1000), "note": note})

    async def upload(self, client: httpx.AsyncClient, feature: str, data: bytes,
                     content_type: str = "image/jpeg", file_name: str = "selfie.jpg") -> str:
        path = f"/s2s/v2.0/file/{feature}"
        t0 = time.time()
        r = await client.post(self.base + path, headers=self._headers(), json={
            "files": [{"content_type": content_type, "file_name": file_name, "file_size": len(data)}]
        })
        body = self._check(r)
        f = body["data"]["files"][0]
        req = f["requests"][0]
        self._log("POST", path, t0, "file_id issued")
        t0 = time.time()
        put = await client.request(req.get("method", "PUT"), req["url"], headers=req.get("headers", {}), content=data)
        if put.status_code >= 300:
            raise YouCamError(f"Upload to pre-signed URL failed ({put.status_code})", status=put.status_code)
        self._log("PUT", "pre-signed upload URL", t0, f"{len(data)//1024} KB")
        return f["file_id"]

    async def run_and_poll(self, client: httpx.AsyncClient, feature: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = f"/s2s/v2.0/task/{feature}"
        t0 = time.time()
        body = self._check(await client.post(self.base + path, headers=self._headers(), json=payload))
        task_id = body["data"]["task_id"]
        self._log("POST", path, t0, "task started")
        deadline = time.time() + settings.youcam_timeout
        polls = 0
        t0 = time.time()
        while time.time() < deadline:
            await asyncio.sleep(settings.youcam_poll_interval)
            polls += 1
            data = self._check(await client.get(f"{self.base}{path}/{task_id}", headers=self._headers()))["data"]
            status = data.get("task_status")
            if status == "success":
                self._log("GET", f"{path}/{{task_id}}", t0, f"success after {polls} polls")
                return data
            if status == "error":
                raise YouCamError(data.get("error_message") or data.get("error") or "task error", code=data.get("error"))
        raise YouCamError(f"{feature} timed out after {settings.youcam_timeout}s", code="timeout")

    # ---------------- features ----------------
    async def skin_analysis(self, image: bytes) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60) as c:
            fid = await self.upload(c, "skin-analysis", image)
            data = await self.run_and_poll(c, "skin-analysis", {
                "src_file_id": fid,
                "dst_actions": SD_SKIN_ACTIONS,
                "miniserver_args": {"enable_mask_overlay": False},
                "format": "json",
            })
        return normalize_skin_results(data.get("results"))

    async def skin_tone(self, image: bytes) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60) as c:
            fid = await self.upload(c, "skin-tone-analysis", image)
            data = await self.run_and_poll(c, "skin-tone-analysis", {
                "src_file_id": fid, "face_angle_strictness_level": "medium",
            })
        res = data.get("results") or {}
        color = res.get("color") or res
        return {"skin_color": color.get("skin_color"), "eye_color_name": color.get("eye_color_name"),
                "hair_color_name": color.get("hair_color_name"), "lip_color": color.get("lip_color"),
                "face_quality": res.get("face_quality")}

    async def makeup_vto(self, image: bytes, effects: list[dict[str, Any]]) -> str:
        async with httpx.AsyncClient(timeout=60) as c:
            fid = await self.upload(c, "makeup-vto", image)
            data = await self.run_and_poll(c, "makeup-vto", {"src_file_id": fid, "version": "1.0", "effects": effects})
        res = data.get("results") or {}
        url = res.get("url") if isinstance(res, dict) else res
        if not url:
            raise YouCamError("makeup-vto returned no result url")
        return url


def normalize_skin_results(results: Any) -> dict[str, Any]:
    """Normalise both documented result shapes into {concerns:{key:{ui_score,raw_score,mask_url}}, overall, skin_age}.

    format=json  -> {"output": [{"type": "pore", "ui_score": 92, "raw_score": 95.3, "mask_urls": [...]}, ...]}
    score_info   -> {"pore": {"ui_score":..}, "hd_pore": {"whole": {...}}, "all": {"score": 75.7}, "skin_age": 37}
    """
    concerns: dict[str, dict[str, Any]] = {}
    overall = None
    skin_age = None
    if isinstance(results, dict) and isinstance(results.get("output"), list):
        for item in results["output"]:
            t = item.get("type")
            if t == "all":
                overall = item.get("score", item.get("ui_score"))
                continue
            if t == "skin_age":
                skin_age = item.get("value", item.get("score"))
                continue
            if t and "ui_score" in item:
                masks = item.get("mask_urls") or []
                concerns[t.replace("hd_", "")] = {
                    "ui_score": item.get("ui_score"), "raw_score": item.get("raw_score"),
                    "mask_url": masks[0] if masks else None,
                }
        overall = overall if overall is not None else results.get("all", {}).get("score") if isinstance(results.get("all"), dict) else overall
        skin_age = skin_age if skin_age is not None else results.get("skin_age")
    elif isinstance(results, dict):
        for k, v in results.items():
            if k == "all" and isinstance(v, dict):
                overall = v.get("score")
            elif k == "skin_age":
                skin_age = v
            elif isinstance(v, dict):
                node = v.get("whole", v) if "ui_score" not in v else v
                if "ui_score" in node:
                    concerns[k.replace("hd_", "")] = {"ui_score": node["ui_score"], "raw_score": node.get("raw_score"),
                                                     "mask_url": None}
    if overall is None and concerns:
        overall = sum(c["ui_score"] for c in concerns.values()) / len(concerns)
    return {"concerns": concerns, "overall": round(overall) if overall is not None else None, "skin_age": skin_age}
