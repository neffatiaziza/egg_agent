# backend/main.py — Version finale
import os
from dotenv import load_dotenv
load_dotenv()

import json
import asyncio
import re
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime

from backend.db.database import SessionLocal, Base, engine
from backend.db.models import Lot, Alert, Feedback, Stock, PartnerOrder, DispatchLog, QualityIncident
from backend.agent.graph import create_egg_agent_graph, store_image, cleanup_image
from backend.agent.prompts import SYSTEM_PROMPT
from backend.tools.rag_tools import client as chroma_client

app = FastAPI(title="Egg Quality Control System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

graph        = create_egg_agent_graph()
session_store = {}
lot_queues   = {}

# ── Routes ───────────────────────────────────────────────────
try:
    from backend.routes.notifications import router as notif_router
    app.include_router(notif_router)
except ImportError:
    pass

try:
    from backend.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    message:        Optional[str]   = "Please run a full quality and grading analysis."
    normal_image:   Optional[str]   = None
    candling_image: Optional[str]   = None
    weight_g:       Optional[float] = None
    height_mm:      Optional[float] = None
    diameter_mm:    Optional[float] = None
    lot_id:         Optional[str]   = None
    lay_date:       Optional[str]   = None
    farm_zone:      Optional[str]   = None
    quantity:       Optional[int]   = 1

class ChatRequest(BaseModel):
    message:    str
    session_id: Optional[str] = None

class FeedbackRequest(BaseModel):
    lot_id:         str
    operator_grade: str
    comment:        str

class OrderRequest(BaseModel):
    partner_name:   str
    required_grade: str
    required_size:  str
    quantity_needed: int
    deadline_date:  str
    priority:       int

class ResolveRequest(BaseModel):
    resolution_notes: str


# ─────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    from backend.db import models
    Base.metadata.create_all(bind=engine)


# ─────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    db = SessionLocal()
    db_ok = True
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    finally:
        db.close()
    return {"status": "ok", "db_ok": db_ok, "chroma_ok": chroma_client is not None}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def extract_final_json(text: str) -> dict:
    if not text or not text.strip():
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0).strip())
        except Exception:
            pass
    return {"natural_language_response": text}


async def pre_analyze_image(image_normal_b64, image_candling_b64) -> dict:
    """Appel Groq Vision AVANT l'agent pour pré-analyser l'image."""
    if not image_normal_b64 and not image_candling_b64:
        return {"status": "no_image", "analysis": None}

    import logging
    from langchain_groq import ChatGroq
    logger = logging.getLogger(__name__)

    vision_llm = ChatGroq(
        model=os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0,
        max_tokens=2000
    )

    content = []
    if image_normal_b64:
        clean = image_normal_b64.split(",")[-1] if "," in image_normal_b64 else image_normal_b64
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean}"}})
        content.append({"type": "text", "text": "[NORMAL LIGHT IMAGE]"})
    if image_candling_b64:
        clean = image_candling_b64.split(",")[-1] if "," in image_candling_b64 else image_candling_b64
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean}"}})
        content.append({"type": "text", "text": "[CANDLING IMAGE]"})

    content.append({"type": "text", "text": """Analyze this egg and return ONLY valid JSON:
{
  "crack_detected": false,
  "crack_severity": "none",
  "blood_spot_detected": false,
  "shell_condition": "clean",
  "shape_anomaly": false,
  "quality_score": 0.8,
  "estimated_mm_length": null,
  "estimated_mm_width": null,
  "air_cell_height_mm": null,
  "air_cell_mobile": null,
  "double_yolk_detected": false,
  "fertilized": false,
  "freshness_estimate": "fresh",
  "defects_observed": [],
  "preliminary_grade": "A",
  "reasoning": "brief explanation"
}"""})

    try:
        response = vision_llm.invoke([HumanMessage(content=content)])
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return {"status": "success", "analysis": json.loads(raw.strip())}
    except Exception as e:
        logger.error(f"Vision pre-analysis failed: {e}")
        return {"status": "failed", "analysis": None, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# /analyze
# ─────────────────────────────────────────────────────────────
@app.post("/analyze")
async def analyze_egg(req: AnalyzeRequest):
    import logging
    logger = logging.getLogger(__name__)

    lot_id = req.lot_id or f"LOT-{uuid.uuid4().hex[:8].upper()}"
    logger.info(f"[ANALYZE] lot_id={lot_id} normal={'YES' if req.normal_image else 'NO'}")

    if lot_id in lot_queues:
        raise HTTPException(status_code=400, detail=f"Lot {lot_id} already being analyzed")

    lot_queues[lot_id] = asyncio.Queue()

    # ── STOCKER L'IMAGE ICI — avant tout ─────────────────
    # Avant même de lancer le graph, on met l'image dans IMAGE_STORE.
    # Comme ça tool_node peut la récupérer peu importe ce que LangGraph fait.
    if req.normal_image:
        store_image(lot_id, req.normal_image, req.candling_image)
        logger.info(f"[ANALYZE] Image stored for {lot_id} ({len(req.normal_image)} chars)")

    asyncio.create_task(process_graph(req, lot_id))
    return {"status": "started", "lot_id": lot_id}


async def process_graph(req: AnalyzeRequest, lot_id: str):
    queue  = lot_queues[lot_id]
    import logging
    logger = logging.getLogger(__name__)

    try:
        config = {"configurable": {"thread_id": lot_id}}

        # Pré-analyse visuelle
        vision_result = await pre_analyze_image(req.normal_image, req.candling_image)

        if vision_result["status"] != "success" or not vision_result.get("analysis"):
            vlm_data       = {}
            image_provided = bool(req.normal_image or req.candling_image)
            vision_summary = "Vision pre-analysis unavailable."
        else:
            vlm_data       = vision_result["analysis"]
            image_provided = True
            vision_summary = f"""
VISION PRE-ANALYSIS:
- crack: {vlm_data.get('crack_detected')} (severity: {vlm_data.get('crack_severity')})
- blood_spot: {vlm_data.get('blood_spot_detected')}
- shell: {vlm_data.get('shell_condition')}
- quality_score: {vlm_data.get('quality_score')}
- fertilized: {vlm_data.get('fertilized')}
- preliminary_grade: {vlm_data.get('preliminary_grade')}
- reasoning: {vlm_data.get('reasoning')}
Full JSON: {json.dumps(vlm_data)}
"""

        message_text = f"""
Lot ID: {lot_id}
Image provided: {image_provided}

{vision_summary}

Execute the mandatory 9-step pipeline:
1. egg_detector(image_input="<base64>")
2. visual_egg_grader(crop_b64="<crop_for_egg_001>")
3. vlm_egg_analyzer(lot_id="{lot_id}")
4. grade_regulation_resolver(predicted_grade="{vlm_data.get('preliminary_grade', 'A')}")
5. egg_grader(cnn_result=<step2>, regulation=<step4>, vlm_result=<step3>, egg_id="egg_0_{lot_id}")
6. inventory_allocator(lot_id="{lot_id}", grade=<step5.final_grade>, size_class="M", destination=<step5.destination>)
7. IF step 6 returns partner_allocated=False AND <step5.final_grade> is not E, execute partner_discovery_tool(grade=<step5.final_grade>, size="M", quantity=1, price_tnd=<step5.market_price_TND>). ELSE skip.
8. alert_and_logger(lot_id="{lot_id}", grade=<step5.final_grade>)
9. report_and_qr_generator(lot_id="{lot_id}", grade=<step5.final_grade>)

Execute ALL steps. Never stop early.
"""
        if req.message and req.message.strip():
            message_text += f"\nUser note: {req.message}"

        input_state = {
            "lot_id":             lot_id,
            "image_normal_b64":   req.normal_image,
            "image_candling_b64": req.candling_image,
            "sensor_data":        {"weight_g": req.weight_g, "height_mm": req.height_mm, "diameter_mm": req.diameter_mm},
            "tool_results":       {},
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=message_text)
            ],
            "iterations":        0,
            "tool_call_counts":  {},
            "lay_date":          req.lay_date,
            "farm_zone":         req.farm_zone,
            "quantity":          req.quantity,
            "vlm_pre_analysis":  vlm_data,
            "image_provided":    image_provided,
            "needs_human_review": False,
            "error_log":         []
        }

        final_message          = None
        collected_tool_results = {}

        async for output in graph.astream(input_state, config=config, stream_mode="updates"):
            for node, state_update in output.items():
                if node == "tools":
                    for msg in state_update.get("messages", []):
                        if getattr(msg, "name", None):
                            await queue.put(json.dumps({
                                "type":   "tool_call",
                                "tool":   msg.name,
                                "result": msg.content
                            }))
                            try:
                                parsed = json.loads(msg.content)
                                if isinstance(parsed, dict):
                                    collected_tool_results[msg.name] = parsed
                            except Exception:
                                pass
                elif node == "agent":
                    msgs = state_update.get("messages", [])
                    if msgs:
                        final_message = msgs[-1].content
            await asyncio.sleep(0.1)

        # Libérer la mémoire
        cleanup_image(lot_id)

        # Construire payload final
        final_data = extract_final_json(final_message) if final_message else {}
        vlm    = collected_tool_results.get("vlm_egg_analyzer", {})
        grader = collected_tool_results.get("egg_grader", {})
        alloc  = collected_tool_results.get("inventory_allocator", {})
        cnn    = collected_tool_results.get("visual_egg_grader", {})

        quality_val   = float(vlm.get("quality_score") or 0.0)
        quality_str   = "good" if quality_val >= 0.8 else ("fair" if quality_val >= 0.5 else "bad")
        is_fertile    = vlm.get("fertilized")
        fertility_str = "fertile" if is_fertile is True else ("infertile" if is_fertile is False else "unknown")

        structured = {
            "lot_id":           lot_id,
            "quality":          quality_str,
            "fertility_status": fertility_str,
            "confidence":       grader.get("confidence") or cnn.get("confidence") or quality_val,
            "defects_detected": vlm.get("defects_observed", []),
            "shell_assessment": vlm.get("shell_condition", "unknown"),
            "size_class":       "M",
            "grade":            grader.get("final_grade"),
            "final_grade":      grader.get("final_grade"),
            "eu_grade":         grader.get("eu_grade"),
            "destination":      grader.get("destination"),
            "recommendation":   grader.get("recommendation"),
            "grading_source":   grader.get("grading_source"),
            "market_price_TND": grader.get("market_price_TND"),
            "needs_human_review": grader.get("needs_human_review", False),
            "routing_decision": alloc.get("routing_decision"),
            "partner_allocated":alloc.get("partner_allocated", False),
            "partner_name":     alloc.get("partner_name"),
            "order_id":         alloc.get("order_id"),
            "allocation_notes": alloc.get("allocation_notes"),
            "natural_language_response": final_message or "",
            "discovery_triggered": "partner_discovery_tool" in collected_tool_results,
            "discovery_result": collected_tool_results.get("partner_discovery_tool", {})
        }
        structured = {k: v for k, v in structured.items() if v is not None}
        merged     = {**final_data, **structured}

        session_store[lot_id] = {
            "lot_id": lot_id,
            "last_analysis":      vlm,
            "last_grader_result": grader,
            "timestamp":          datetime.utcnow().isoformat()
        }

        await queue.put(json.dumps({"type": "final", "data": merged}))

    except Exception as e:
        logger.error(f"[{lot_id}] process_graph error: {e}", exc_info=True)
        cleanup_image(lot_id)
        await queue.put(json.dumps({"type": "error", "error": str(e)}))
    finally:
        await queue.put(None)


# ─────────────────────────────────────────────────────────────
# SSE
# ─────────────────────────────────────────────────────────────
@app.get("/api/stream/{lot_id}")
async def stream_analysis(lot_id: str):
    if lot_id not in lot_queues:
        lot_queues[lot_id] = asyncio.Queue()

    async def event_generator():
        queue = lot_queues[lot_id]
        while True:
            item = await queue.get()
            if item is None:
                lot_queues.pop(lot_id, None)
                break
            yield {"data": item}

    return EventSourceResponse(event_generator())


# ─────────────────────────────────────────────────────────────
# /chat
# ─────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id or f"CHAT-{uuid.uuid4().hex[:8].upper()}"
    if session_id not in lot_queues:
        lot_queues[session_id] = asyncio.Queue()
    asyncio.create_task(_process_chat(req.message, session_id))
    return {"status": "started", "session_id": session_id}


async def _process_chat(message: str, session_id: str):
    queue = lot_queues[session_id]
    try:
        config      = {"configurable": {"thread_id": session_id}}
        input_state = {
            "lot_id": session_id,
            "image_normal_b64": None, "image_candling_b64": None,
            "sensor_data": {}, "tool_results": {},
            "messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=message)],
            "iterations": 0, "tool_call_counts": {},
            "vlm_pre_analysis": {}, "image_provided": False,
            "needs_human_review": False, "error_log": []
        }

        final_message = ""
        collected     = {}

        async for output in graph.astream(input_state, config=config, stream_mode="updates"):
            for node, state_update in output.items():
                if node == "tools":
                    for msg in state_update.get("messages", []):
                        if getattr(msg, "name", None):
                            await queue.put(json.dumps({"type": "tool_call", "tool": msg.name, "result": msg.content}))
                            try:
                                collected[msg.name] = json.loads(msg.content)
                            except Exception:
                                pass
                elif node == "agent":
                    msgs = state_update.get("messages", [])
                    if msgs:
                        final_message = msgs[-1].content
            await asyncio.sleep(0.05)

        await queue.put(json.dumps({"type": "final", "data": {"session_id": session_id, "response": final_message, "tool_results": collected}}))
    except Exception as e:
        await queue.put(json.dumps({"type": "error", "error": str(e)}))
    finally:
        await queue.put(None)


@app.get("/api/chat/stream/{session_id}")
async def stream_chat(session_id: str):
    if session_id not in lot_queues:
        lot_queues[session_id] = asyncio.Queue()

    async def event_generator():
        queue = lot_queues[session_id]
        while True:
            item = await queue.get()
            if item is None:
                lot_queues.pop(session_id, None)
                break
            yield {"data": item}

    return EventSourceResponse(event_generator())


# ─────────────────────────────────────────────────────────────
# Lots / Stats
# ─────────────────────────────────────────────────────────────
@app.get("/lot/{lot_id}")
def get_lot(lot_id: str):
    db  = SessionLocal()
    lot = db.query(Lot).filter(Lot.lot_id == lot_id).first()
    db.close()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot

@app.get("/lot/{lot_id}/report")
def get_lot_report(lot_id: str):
    path = os.path.join("backend", "reports", f"{lot_id}_report.pdf")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return FileResponse(path, media_type="application/pdf", filename=f"{lot_id}_report.pdf")

@app.get("/lots")
def get_lots():
    db   = SessionLocal()
    lots = db.query(Lot).order_by(Lot.timestamp.desc()).all()
    db.close()
    return lots

@app.get("/stats")
def get_stats():
    db    = SessionLocal()
    today = datetime.utcnow().date()
    all_today = db.query(Lot).filter(Lot.timestamp >= datetime(today.year, today.month, today.day)).all()
    REJECTED  = {"E", "D", "Rejected", "Oeuf industriel", "UNGRADED"}
    total     = len(all_today)
    rejected  = sum(1 for l in all_today if l.grade in REJECTED)
    grade_dist = {}
    confidences = []
    for l in all_today:
        grade_dist[l.grade] = grade_dist.get(l.grade, 0) + 1
        if l.confidence:
            confidences.append(l.confidence)
    db.close()
    return {
        "total_lots_today":  total,
        "rejection_rate":    rejected / total if total > 0 else 0.0,
        "grade_distribution":[{"name": k, "count": v} for k, v in grade_dist.items()],
        "avg_confidence":    sum(confidences) / len(confidences) if confidences else 0.0
    }

@app.post("/feedback")
def post_feedback(req: FeedbackRequest):
    db = SessionLocal()
    fb = Feedback(lot_id=req.lot_id, operator_grade=req.operator_grade, comment=req.comment)
    db.add(fb); db.commit(); db.close()
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────
# Logistics
# ─────────────────────────────────────────────────────────────
@app.get("/logistics/stock")
def get_logistics_stock():
    db = SessionLocal()
    from sqlalchemy import func
    from datetime import timedelta
    now_plus_5 = datetime.utcnow() + timedelta(days=5)
    results  = db.query(Stock.grade, func.sum(Stock.quantity).label("total_quantity")).filter(Stock.status == "available").group_by(Stock.grade).all()
    expiring = db.query(Stock.grade, func.count(Stock.id).label("n")).filter(Stock.status == "available", Stock.expiry_date <= now_plus_5).group_by(Stock.grade).all()
    db.close()
    stock_map = {}
    for r in results:
        zone = "Zone-Froid-A" if r.grade in ("AA","A") else ("Zone-C" if r.grade in ("C","D","E") else "Zone-B")
        stock_map[r.grade] = {"quantity": r.total_quantity, "storage_zone": zone, "expiring_lots": 0}
    for e in expiring:
        if e.grade in stock_map:
            stock_map[e.grade]["expiring_lots"] = e.n
    return stock_map

@app.get("/logistics/orders")
def get_logistics_orders():
    db     = SessionLocal()
    orders = db.query(PartnerOrder).order_by(PartnerOrder.priority.asc(), PartnerOrder.deadline_date.asc()).all()
    db.close()
    return [{"id": o.id, "partner_name": o.partner_name, "required_grade": o.required_grade,
             "required_size": o.required_size, "quantity_needed": o.quantity_needed,
             "quantity_fulfilled": o.quantity_fulfilled,
             "fulfillment_percentage": (o.quantity_fulfilled/o.quantity_needed*100) if o.quantity_needed else 0,
             "deadline_date": o.deadline_date, "status": o.status, "priority": o.priority}
            for o in orders]

@app.post("/logistics/orders")
def create_logistics_order(req: OrderRequest):
    db = SessionLocal()
    from dateutil import parser
    order = PartnerOrder(
        partner_name=req.partner_name, required_grade=req.required_grade,
        required_size=req.required_size, quantity_needed=req.quantity_needed,
        quantity_fulfilled=0, deadline_date=parser.parse(req.deadline_date),
        status="pending", priority=req.priority
    )
    db.add(order); db.commit(); db.close()
    return {"status": "ok"}

@app.get("/logistics/incidents")
def get_logistics_incidents(resolved: Optional[bool] = False):
    db        = SessionLocal()
    incidents = db.query(QualityIncident).filter(QualityIncident.resolved == resolved).all()
    db.close()
    return incidents

@app.post("/logistics/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int, req: ResolveRequest):
    db       = SessionLocal()
    incident = db.query(QualityIncident).filter(QualityIncident.id == incident_id).first()
    if not incident:
        db.close()
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.resolved         = True
    incident.resolution_notes = req.resolution_notes
    db.commit(); db.close()
    return {"status": "ok"}

@app.get("/logistics/dispatch-log")
def get_dispatch_log(partner_name: Optional[str] = None, date: Optional[str] = None):
    db    = SessionLocal()
    from sqlalchemy import func
    query = db.query(DispatchLog)
    if partner_name:
        query = query.filter(DispatchLog.partner_name == partner_name)
    if date:
        query = query.filter(func.date(DispatchLog.dispatched_at) == date)
    logs = query.all()
    db.close()
    return logs