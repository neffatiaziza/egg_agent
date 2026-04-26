import os
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func
from pydantic import BaseModel

from backend.db.database import SessionLocal
from backend.db.models import Lot
from groq import Groq

router = APIRouter()

def get_lot_from_db(lot_id: str):
    db = SessionLocal()
    lot = db.query(Lot).filter(Lot.lot_id == lot_id).first()
    db.close()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    # convert SQLAlchemy to dict
    lot_dict = {c.name: getattr(lot, c.name) for c in lot.__table__.columns}
    return lot_dict

def generate_remarks(lot: dict) -> list:
    remarks = []
    grade = lot.get("grade")
    confidence = lot.get("confidence", 0)
    defects = lot.get("defects_detected")
    if defects:
        try:
            if isinstance(defects, str):
                defects = json.loads(defects)
        except:
            defects = [defects]
    else:
        defects = []
        
    fertility = lot.get("fertility_status")
    destination = lot.get("destination")
    needs_review = lot.get("needs_human_review", 0)

    if grade == "C":
        remarks.append({"level": "error", "text": "⚠️ Does not meet UNECE EGG-1 / INNORPI export standards. Must be rejected or redirected."})
    if grade == "AA":
        remarks.append({"level": "success", "text": "🌟 Premium quality. Priority candidate for high-value market or export."})
    if fertility == "fertile" and grade in ["AA", "A"]:
        remarks.append({"level": "success", "text": "✅ Excellent hatchery candidate. Route to incubators."})
    if fertility == "fertile" and grade == "B":
        remarks.append({"level": "warning", "text": "⚠️ Fertile but below Grade A. Evaluate hatchery policy before routing."})
    if confidence is not None and confidence < 0.6:
        remarks.append({"level": "warning", "text": "🔍 Low confidence score. Manual inspection recommended."})
    if defects and len(defects) > 0:
        remarks.append({"level": "error", "text": f"🔴 Defects detected: {', '.join(defects)}. Review before dispatch."})
    if destination == "Rejected":
        remarks.append({"level": "error", "text": "❌ Lot rejected. Do not ship. Ensure traceability audit log is complete."})
    if needs_review:
        remarks.append({"level": "warning", "text": "👁️ Flagged for supervisor review. Validate before any routing action."})
    return remarks

@router.get("/api/lots")
def get_lots(
    grade: str = None,
    quality: str = None,
    fertility_status: str = None,
    destination: str = None,
    from_date: str = None,
    to_date: str = None,
    search: str = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "timestamp",
    sort_order: str = "desc"
):
    db = SessionLocal()
    query = db.query(Lot)
    
    if grade and grade.lower() != "all":
        query = query.filter(Lot.grade == grade)
    if quality and quality.lower() != "all":
        query = query.filter(Lot.quality == quality)
    if fertility_status and fertility_status.lower() != "all":
        query = query.filter(Lot.fertility_status == fertility_status)
    if destination and destination.lower() != "all":
        query = query.filter(Lot.destination == destination)
    if search:
        query = query.filter(Lot.lot_id.ilike(f"%{search}%"))
        
    # Date filters
    if from_date:
        try:
            fd = datetime.fromisoformat(from_date)
            query = query.filter(Lot.timestamp >= fd)
        except: pass
    if to_date:
        try:
            td = datetime.fromisoformat(to_date)
            query = query.filter(Lot.timestamp <= td)
        except: pass

    total = query.count()
    
    if sort_order == "asc":
        query = query.order_by(getattr(Lot, sort_by).asc())
    else:
        query = query.order_by(getattr(Lot, sort_by).desc())
        
    lots = query.offset((page - 1) * page_size).limit(page_size).all()
    
    items = []
    for lot in lots:
        lot_dict = {c.name: getattr(lot, c.name) for c in lot.__table__.columns}
        if lot_dict.get("defects_detected"):
            try:
                lot_dict["defects_detected"] = json.loads(lot_dict["defects_detected"])
            except: pass
        if lot_dict.get("reasoning_trace"):
            try:
                lot_dict["reasoning_trace"] = json.loads(lot_dict["reasoning_trace"])
            except: pass
        items.append(lot_dict)
        
    db.close()
    return {"total": total, "page": page, "items": items}

@router.get("/api/lots/{lot_id}")
def get_lot_detail(lot_id: str):
    lot_dict = get_lot_from_db(lot_id)
    
    if lot_dict.get("defects_detected"):
        try:
            lot_dict["defects_detected"] = json.loads(lot_dict["defects_detected"])
        except: pass
    if lot_dict.get("reasoning_trace"):
        try:
            lot_dict["reasoning_trace"] = json.loads(lot_dict["reasoning_trace"])
        except: pass

    remarks = generate_remarks(lot_dict)
    
    return {"lot": lot_dict, "remarks": remarks}

@router.get("/api/dashboard/stats")
def get_dashboard_stats():
    db = SessionLocal()
    
    total_lots = db.query(Lot).count()
    
    today = datetime.utcnow().date()
    today_start = datetime(today.year, today.month, today.day)
    today_count = db.query(Lot).filter(Lot.timestamp >= today_start).count()
    
    human_review_count = db.query(Lot).filter(Lot.needs_human_review == 1).count()
    
    # Grade dist
    grades = db.query(Lot.grade, func.count(Lot.id)).group_by(Lot.grade).all()
    grade_distribution = {g[0] if g[0] else "Unknown": g[1] for g in grades}
    
    grade_percentages = {}
    if total_lots > 0:
        grade_percentages = {k: (v/total_lots)*100 for k, v in grade_distribution.items()}
    
    # Avg confidence
    avg_conf = db.query(func.avg(Lot.confidence)).scalar() or 0.0
    
    # Fertility
    fertile_count = db.query(Lot).filter(Lot.fertility_status == "fertile").count()
    fertility_rate = (fertile_count / total_lots * 100) if total_lots > 0 else 0.0
    
    # Destination
    dests = db.query(Lot.destination, func.count(Lot.id)).group_by(Lot.destination).all()
    destination_breakdown = {d[0] if d[0] else "Unknown": d[1] for d in dests}
    
    db.close()
    
    return {
      "total_lots": total_lots,
      "grade_distribution": grade_distribution,
      "grade_percentages": grade_percentages,
      "avg_confidence": avg_conf,
      "fertility_rate": fertility_rate,
      "human_review_count": human_review_count,
      "today_count": today_count,
      "destination_breakdown": destination_breakdown
    }

@router.get("/api/dashboard/charts")
def get_chart_data():
    db = SessionLocal()
    
    # Daily lots (last 30 days)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    # Using SQLite date function for grouping
    daily = db.query(func.date(Lot.timestamp).label("date"), func.count(Lot.id)).filter(Lot.timestamp >= thirty_days_ago).group_by("date").order_by("date").all()
    daily_lots = [{"date": d[0], "count": d[1]} for d in daily if d[0]]
    
    # Confidence histogram
    c_0_20 = db.query(Lot).filter(Lot.confidence >= 0.0, Lot.confidence < 0.20).count()
    c_20_40 = db.query(Lot).filter(Lot.confidence >= 0.20, Lot.confidence < 0.40).count()
    c_40_60 = db.query(Lot).filter(Lot.confidence >= 0.40, Lot.confidence < 0.60).count()
    c_60_80 = db.query(Lot).filter(Lot.confidence >= 0.60, Lot.confidence < 0.80).count()
    c_80_100 = db.query(Lot).filter(Lot.confidence >= 0.80).count()
    
    confidence_histogram = [
        {"range": "0-20%", "count": c_0_20},
        {"range": "20-40%", "count": c_20_40},
        {"range": "40-60%", "count": c_40_60},
        {"range": "60-80%", "count": c_60_80},
        {"range": "80-100%", "count": c_80_100},
    ]
    
    # Grade pie
    grades = db.query(Lot.grade, func.count(Lot.id)).group_by(Lot.grade).all()
    grade_pie = [{"name": g[0] if g[0] else "Unknown", "value": g[1]} for g in grades]
    
    # Destination bar
    dests = db.query(Lot.destination, func.count(Lot.id)).group_by(Lot.destination).all()
    destination_bar = [{"name": d[0] if d[0] else "Unknown", "value": d[1]} for d in dests]

    db.close()
    
    return {
      "daily_lots": daily_lots,
      "confidence_histogram": confidence_histogram,
      "grade_pie": grade_pie,
      "destination_bar": destination_bar
    }

@router.post("/api/lots/{lot_id}/recommendations")
def generate_ai_recommendations(lot_id: str):
    lot = get_lot_from_db(lot_id)
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{
            "role": "user",
            "content": f"You are an egg quality control expert. Based on this lot analysis:\n{json.dumps(lot, indent=2, default=str)}\n\nGive 3 to 5 concrete, actionable recommendations for this specific lot.\nConsider: routing decision, storage conditions, regulatory compliance \n(UNECE EGG-1 / INNORPI), market destination, and risk mitigation.\nFormat as a numbered list. Be concise and professional."
        }],
        max_tokens=500,
        temperature=0.3
    )
    return {"recommendations": response.choices[0].message.content}
