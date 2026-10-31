# SkinSync — Devpost submission draft

**Tagline:** Selfie in, routine out. YouCam Skin AI plus a shopping agent plus virtual try-on, so you buy the right products the first time.

**Track:** Skin AI (combined with Makeup VTO)
**Built with:** YouCam AI API v2 (Skin Analysis, Skin Tone Analysis, Makeup Virtual Try-On), Python, FastAPI, httpx, Pillow, Groq (gpt-oss-120b, OpenAI-compatible tool calling), vanilla HTML/CSS/JS, Docker, Render
**Links:** Repo: https://github.com/abidhussain19932-stack/skinsync · Live demo: https://skinsync-l2iu.onrender.com · Video: https://youtu.be/e5c_rb_vHUw

---

## Inspiration
Most skincare purchases happen at one moment: in front of a shelf (or a search page) of 40 serums, often right after a breakout. Skin-analysis apps give you a score and stop there. Shops give you a search box and let you guess. The two never connect, so people buy by hype, stack products that clash, and return foundation that was the wrong shade. We wanted one flow that answers *"what is going on with my skin, what should I actually do, what should I buy, and how will it look on me?"* in under a minute.

## What it does
1. **Snap a selfie** (or use the sample). Pick a budget and goals such as "calm redness" or "clear breakouts", plus sensitive or fragrance-free filters.
2. **YouCam Skin Analysis** scores 14 concerns (redness, acne, pores, oiliness, texture, dark circles, hydration and more), and **YouCam Skin Tone Analysis** reads the skin colour.
3. **The SkinSync agent** (an LLM with tools) explains the top 3–4 findings in kind, plain language. It then shops the catalog with batched tool calls and builds a **morning and evening routine** within your budget: sunscreen every morning, one targeted night treatment, no clashing actives. It also matches your **foundation shade** to your measured skin tone.
4. **YouCam Makeup Virtual Try-On** renders the recommended look (Fresh Dewy, Soft Glam or Office Matte) on *your* photo, using the exact lipstick, blush and foundation SKUs, with a before/after slider. Swap shades and re-render.
5. **Refine in plain English**, for example "I'm pregnant, no retinoids" or "keep it under $60" or "bolder for a party". The agent re-plans and the try-on updates.
6. **Add the routine or the look to the bag** in one tap. A "How the agent got here" panel shows every YouCam call and agent tool call.

**Who it's for:** shoppers who feel overwhelmed by skincare choices, and beauty retailers who want higher conversion, fewer returns (shade-matched base, try before you buy) and bigger baskets (a full routine instead of a single SKU).

## How we built it
- **Backend:** FastAPI with an async YouCam v2 client (`httpx`). Every feature follows the documented flow: `POST /s2s/v2.0/file/{feature}` → `PUT` to the pre-signed URL → `POST /s2s/v2.0/task/{feature}` → poll `GET /s2s/v2.0/task/{feature}/{task_id}` until `success`/`error`, with `Authorization: Bearer <API key>`. Skin Analysis and Skin Tone run **in parallel**. Images are EXIF-corrected and resized to each feature's documented limits: SD skin needs a short side of at least 480 px, and makeup VTO needs width under 1920 and height under 1080.
- **Skin Analysis:** all 14 SD `dst_actions` with `format: "json"`. A normaliser handles both the JSON `output[]` shape and the ZIP `score_info.json` shape (HD subcategories included).
- **Agent:** Groq `openai/gpt-oss-120b` with OpenAI-style tool calling. A batched `search_products` tool (multi-query plus foundation shade match) means a plan usually takes only **two model turns** and about 3 seconds. `submit_plan` returns a structured plan. The server then **validates** every SKU (hallucinated ids are dropped) and runs a **budget self-check**, sending an over-budget plan back to the agent once to revise. A deterministic rule engine takes over if the LLM is unavailable.
- **Makeup VTO:** a small compiler turns *look + chosen SKUs* into a YouCam `effects` array (`skin_smooth`, `foundation`, `blush` with pattern `1color1`, `lip_color` with texture-specific required fields such as `gloss` and `transparencyIntensity`).
- **Frontend:** one responsive page in vanilla JS, with no build step. It has a progress stepper, a score ring, concern bars, routine tabs, a before/after slider and a bag drawer.
- **DEMO_MODE:** canned responses with the same shapes as the live API, so judges can run the whole flow without a key.
- **Tests:** pytest plus respx mocks of the YouCam contract (headers, payloads, polling, error propagation) and the full app flow.

## Challenges we ran into
- **Turning scores into advice without overclaiming.** YouCam's `ui_score` is "higher is healthier", so a 56 on redness is the headline, not a 56% redness level. We wrote the agent prompt and the UI to frame this kindly and keep it cosmetic, never diagnostic.
- **Free-tier LLM limits.** Groq's 8k tokens per minute ruled out a chatty agent. We redesigned the tools so one batched call covers every category, cut tool-result payloads, used low reasoning effort, and added a rule-based fallback. Strict tool-schema validation on the provider side also rejected whole calls over a single bad enum, so we moved validation server-side and let the agent self-correct.
- **The makeup VTO schema is detailed.** Pattern names must match catalog labels, and some textures require extra fields. We built the effects compiler to always emit valid combinations.
- **Different size rules per feature.** Skin analysis has a minimum size and VTO has a maximum, so each call gets its own resized copy.

## Accomplishments that we're proud of
- Three YouCam APIs chained into **one coherent shopping journey**: diagnose, explain, prescribe, match the shade, try on, buy.
- A real agent loop with tool use, SKU validation and a budget self-check that finishes in about 3 seconds.
- Try-on that shows **the exact products in your basket**, not a generic filter.
- A runnable demo with zero keys, plus a tested live client.

## What we learned
- How the YouCam v2 file→task→poll pattern works across features, and how much the input photo matters (face width over 60% of the frame, even lighting, bare skin).
- Agents become more reliable when you **validate on the server and feed errors back**, instead of hoping the model gets every field right.
- In beauty commerce, the try-on moment is where confidence turns into a purchase. Pairing it with skin data makes the recommendation feel personal rather than pushy.

## What's next for SkinSync
- **"Is it working?" check-ins:** re-scan every 2–4 weeks and chart each concern's score, so shoppers know whether to keep, swap or stop a product.
- **YouCam AI Skin Simulation** to preview the expected improvement before buying.
- **Live camera try-on** with the YouCam JS Camera Kit, plus HD skin analysis for capable devices.
- A real merchant catalog feed (Shopify or WooCommerce) and real checkout.
- Ingredient-conflict and pregnancy-safety rules as a dedicated agent tool, plus multilingual explanations (Hindi first).
- Outfit and accessory VTO, so the makeup look and the outfit can be styled together.

---

### Screenshots (upload in this order)
1. `screenshots/01-upload.png`: landing page with budget and goals
2. `screenshots/02-analysis.png`: YouCam scores and agent explanation
3. `screenshots/03-tryon.png`: before/after makeup try-on with shade picker
4. `screenshots/04-routine.png`: AM/PM routine with add-to-bag
5. `screenshots/05-agent-trace.png`: refine box and agent/YouCam trace
6. `screenshots/06-mobile.png`: mobile layout
7. `screenshots/07-live-youcam-vto.png`: live YouCam makeup-vto output (before/after)

---

## 2-minute demo video script

**Setup:** run with a real `YOUCAM_API_KEY` if possible (the badge says "Live · YouCam"), otherwise DEMO_MODE. Browser at 1280×800 or a phone. Use no background music, or only royalty-free tracks. Show no third-party brand logos.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00–0:12 | Landing page | "Forty serums on the shelf, and no idea which one is for you. SkinSync turns one selfie into a routine you can actually buy, and lets you try the look before you pay." |
| 0:12–0:25 | Upload a selfie (or the sample), set the budget to $90, tap "Calm redness", then Analyze | "I upload a selfie, set a budget and a goal. SkinSync sends the photo to the YouCam AI API: Skin Analysis scores fourteen concerns, and Skin Tone Analysis reads my exact skin colour, both in parallel." |
| 0:25–0:50 | Progress stepper, then results: score ring, skin age, top concerns, expand "All 14 YouCam scores" | "Here are YouCam's scores. Higher is healthier, so redness at 56 and breakouts at 58 are my focus areas. Instead of leaving me with numbers, our AI agent explains each one in plain language." |
| 0:50–1:10 | Routine tabs, Morning then Evening, then "Add routine to bag" | "The agent shops the catalog with tool calls and builds a morning and evening routine (read out the actives on screen, for example niacinamide by day and azelaic acid at night), with sunscreen every morning, all under my ninety-dollar budget. The server checks the budget and sends the plan back if it's over. One tap adds the whole routine." |
| 1:10–1:35 | Try-on: drag the before/after slider, switch lip shade, tap Re-render | "Next, YouCam Makeup Virtual Try-On. The agent matched my foundation shade to the tone YouCam measured, and picked a blush and lipstick. This is my face wearing those exact products. I can swap the shade and re-render." |
| 1:35–1:52 | Refine chip "I'm pregnant — no retinoids", then the plan updates; open "How the agent got here" | "I can talk to it, too. 'I'm pregnant, no retinoids.' The agent re-plans with pregnancy-friendly picks. And here is every YouCam call and agent step, fully transparent." |
| 1:52–2:00 | Bag drawer, then Checkout | "Diagnose, explain, prescribe, try on, buy, in one flow. That's SkinSync, built on the YouCam AI API." |

**Recording tips:** use OBS or the Windows Game Bar (Win+G) at 1080p. Keep the cursor slow. Cut waiting time but keep one real "Rendering with YouCam…" moment to show it's live. Upload to YouTube as **Public** and paste the link into Devpost.
