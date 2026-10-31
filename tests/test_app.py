import asyncio
import io
import json
import os

os.environ["DEMO_MODE"] = "true"
os.environ["LLM_ENABLED"] = "false"  # deterministic rule planner for unit tests

import httpx  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import agent, catalog  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.youcam import SD_SKIN_ACTIONS, YouCamClient, normalize_skin_results  # noqa: E402

client = TestClient(app)


def _jpeg(w=600, h=800, color=(200, 160, 140)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def test_health_reports_demo():
    r = client.get("/api/health").json()
    assert r["demo_mode"] is True
    assert "makeup-vto" in r["youcam_features"]


def test_full_demo_flow_with_sample():
    a = client.post("/api/analyze", data={"sample": "true"}).json()
    assert len(a["analysis"]["concerns"]) == 14
    assert a["analysis"]["overall"] and a["tone"]["skin_color"].startswith("#")
    sid = a["session_id"]
    p = client.post("/api/plan", json={"session_id": sid, "prefs": {"budget": 90}}).json()
    assert p["am_routine"] and p["pm_routine"]
    assert p["am_routine"][-1]["product"]["category"] == "sunscreen"
    assert p["basket_total"] > 0
    assert p["look"]["foundation"]["category"] == "foundation"
    t = client.post("/api/tryon", json={"session_id": sid}).json()
    cats = [e["category"] for e in t["effects"]]
    assert cats[0] == "skin_smooth" and "lip_color" in cats and "foundation" in cats
    img = client.get(t["after_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/image/{sid}/before").status_code == 200


def test_upload_flow_and_refine():
    r = client.post("/api/analyze", files={"file": ("me.jpg", _jpeg(), "image/jpeg")}).json()
    sid = r["session_id"]
    client.post("/api/plan", json={"session_id": sid, "prefs": {}})
    p = client.post("/api/refine", json={"session_id": sid, "instruction": "cheaper please"}).json()
    assert p["engine"] == "rules"
    t = client.post("/api/tryon", json={"session_id": sid, "look_id": "soft-glam", "lip_id": "lp-03"}).json()
    assert t["look_id"] == "soft-glam"
    assert any(e.get("palettes", [{}])[0].get("color") == "#8E2F4F" for e in t["effects"] if e["category"] == "lip_color")


def test_rejects_non_image():
    r = client.post("/api/analyze", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400


def test_normalize_json_and_score_info_shapes():
    j = normalize_skin_results({"output": [
        {"type": "texture", "ui_score": 68, "raw_score": 57.3, "mask_urls": ["https://x/t.jpg"]},
        {"type": "pore", "ui_score": 92, "raw_score": 95.3, "mask_urls": []}]})
    assert j["concerns"]["texture"]["mask_url"] == "https://x/t.jpg" and j["overall"] == 80
    s = normalize_skin_results({"hd_pore": {"whole": {"ui_score": 67, "raw_score": 49.2}},
                                "hd_redness": {"ui_score": 77, "raw_score": 72.0},
                                "all": {"score": 75.75}, "skin_age": 37})
    assert s["concerns"]["pore"]["ui_score"] == 67 and s["overall"] == 76 and s["skin_age"] == 37


def test_finalize_drops_hallucinated_products():
    analysis = {"concerns": {"acne": {"ui_score": 50}}}
    out = agent.finalize({"am_routine": [{"step": "x", "product_id": "FAKE-1", "why": ""},
                                         {"step": "Protect", "product_id": "sp-01", "why": "spf"}],
                          "pm_routine": [], "concerns": [{"key": "hd_acne", "explanation": "e"}],
                          "look": {"look_id": "nope", "lip_id": "cl-01"}}, analysis, [], "llm:test")
    assert [s["product"]["id"] for s in out["am_routine"]] == ["sp-01"]
    assert out["look"]["look_id"] == "fresh-dewy" and out["look"]["lip"]["category"] == "lipstick"
    assert out["concerns"][0]["key"] == "acne"


def test_foundation_match_is_nearest():
    assert catalog.match_foundation("#F0D2BF", 1)[0]["id"] == "fd-01"
    assert catalog.match_foundation("#5B3722", 1)[0]["id"] == "fd-09"


@respx.mock
def test_youcam_client_live_flow_contract():
    base = settings.youcam_base_url
    settings.youcam_poll_interval = 0
    file_route = respx.post(f"{base}/s2s/v2.0/file/skin-analysis").mock(return_value=httpx.Response(200, json={
        "status": 200, "data": {"files": [{"file_id": "FID", "requests": [
            {"method": "PUT", "url": "https://upload.example/put", "headers": {"Content-Type": "image/jpeg"}}]}]}}))
    put_route = respx.put("https://upload.example/put").mock(return_value=httpx.Response(200))
    task_route = respx.post(f"{base}/s2s/v2.0/task/skin-analysis").mock(
        return_value=httpx.Response(200, json={"status": 200, "data": {"task_id": "TID"}}))
    respx.get(f"{base}/s2s/v2.0/task/skin-analysis/TID").mock(side_effect=[
        httpx.Response(200, json={"status": 200, "data": {"task_status": "running"}}),
        httpx.Response(200, json={"status": 200, "data": {"task_status": "success", "results": {"output": [
            {"type": "acne", "ui_score": 61, "raw_score": 40.1, "mask_urls": ["https://m/acne.png"]}]}}})])
    yc = YouCamClient(api_key="KEY")
    out = asyncio.run(yc.skin_analysis(_jpeg()))
    assert out["concerns"]["acne"]["ui_score"] == 61
    assert file_route.calls[0].request.headers["Authorization"] == "Bearer KEY"
    body = json.loads(task_route.calls[0].request.content)
    assert body["src_file_id"] == "FID" and body["format"] == "json" and body["dst_actions"] == SD_SKIN_ACTIONS
    assert put_route.called
    assert [c["endpoint"] for c in yc.calls][0] == "/s2s/v2.0/file/skin-analysis"


@respx.mock
def test_youcam_vto_error_surfaces():
    base = settings.youcam_base_url
    settings.youcam_poll_interval = 0
    respx.post(f"{base}/s2s/v2.0/file/makeup-vto").mock(return_value=httpx.Response(200, json={
        "status": 200, "data": {"files": [{"file_id": "F", "requests": [{"method": "PUT", "url": "https://u/p", "headers": {}}]}]}}))
    respx.put("https://u/p").mock(return_value=httpx.Response(200))
    respx.post(f"{base}/s2s/v2.0/task/makeup-vto").mock(return_value=httpx.Response(200, json={"status": 200, "data": {"task_id": "T"}}))
    respx.get(f"{base}/s2s/v2.0/task/makeup-vto/T").mock(return_value=httpx.Response(200, json={
        "status": 200, "data": {"task_status": "error", "error": "error_face_position_too_small"}}))
    import pytest
    from app.youcam import YouCamError
    with pytest.raises(YouCamError) as ei:
        asyncio.run(YouCamClient(api_key="K").makeup_vto(_jpeg(), [{"category": "skin_smooth"}]))
    assert ei.value.code == "error_face_position_too_small"


def test_rule_fallback_respects_refine_instruction():
    from app import demo
    a, t = demo.skin_analysis(b"x", True), demo.skin_tone(b"x", True)
    p = agent.rule_plan(a, t, {"budget": 90}, "I'm pregnant, no retinoids, make it bolder for a party")
    ids = [s["product_id"] for s in p["am_routine"] + p["pm_routine"]]
    assert "tr-01" not in ids and "cl-02" not in ids
    assert p["look"]["look_id"] == "soft-glam"
