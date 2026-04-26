from sqlalchemy import Boolean, Column, Float, Integer, String, Text, DateTime, JSON
from datetime import datetime
from backend.db.database import Base


class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(String, unique=True, index=True)
    grade = Column(String)
    quality = Column(String)
    fertility_status = Column(String)
    confidence = Column(Float)
    reasoning_trace = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    needs_human_review = Column(Integer, default=0)
    destination = Column(String, nullable=True)
    size_class = Column(String, nullable=True)
    weight_g = Column(Float, nullable=True)
    defects_detected = Column(String, nullable=True)
    grading_reasoning = Column(String, nullable=True)
    shell_assessment = Column(String, nullable=True)
    internal_assessment = Column(String, nullable=True)
    partner_id = Column(String, nullable=True)
    allocated_to_order = Column(String, nullable=True)
    lot_status = Column(String, nullable=True)
    qr_code_path = Column(String, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(String, index=True)
    rejection_rate = Column(Float)
    message = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(String, index=True)
    operator_grade = Column(String)
    comment = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)


class Stock(Base):
    __tablename__ = "stock"
    id = Column(Integer, primary_key=True, autoincrement=True)
    lot_id = Column(String, index=True)
    grade = Column(String)
    size_class = Column(String)
    quantity = Column(Integer)
    entry_date = Column(DateTime, default=datetime.utcnow)
    expiry_date = Column(DateTime)
    storage_zone = Column(String)
    status = Column(String)
    size_source = Column(String, nullable=True)
    size_confidence = Column(String, nullable=True)
    allocated_to_order = Column(String, nullable=True)


class PartnerOrder(Base):
    __tablename__ = "partner_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    partner_name = Column(String)
    required_grade = Column(String)
    required_size = Column(String)
    quantity_needed = Column(Integer)
    quantity_fulfilled = Column(Integer, default=0)
    deadline_date = Column(DateTime)
    status = Column(String)
    priority = Column(Integer)


class DispatchLog(Base):
    __tablename__ = "dispatch_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    lot_id = Column(String)
    partner_name = Column(String)
    grade = Column(String)
    quantity = Column(Integer)
    dispatched_at = Column(DateTime, default=datetime.utcnow)
    order_id = Column(Integer)


class QualityIncident(Base):
    __tablename__ = "quality_incidents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    lot_id = Column(String)
    defect_type = Column(String)
    weight_g = Column(Float, nullable=True)
    diameter_mm = Column(Float, nullable=True)
    height_mm = Column(Float, nullable=True)
    farm_zone = Column(String, nullable=True)
    root_cause_hypothesis = Column(String)
    alert_category = Column(String)
    severity = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved = Column(Boolean, default=False)
    resolution_notes = Column(Text, nullable=True)
    web_search_sources_used = Column(Text, nullable=True)
    compliance_notes = Column(Text, nullable=True)
    regulatory_basis = Column(String, nullable=True)
    crack_severity = Column(String, nullable=True)


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(String, unique=True, index=True)
    partner_id = Column(String, index=True)
    lot_id = Column(String, index=True)
    order_id = Column(String, index=True)
    total_HT = Column(Float)
    tva_amount = Column(Float)
    total_TTC = Column(Float)
    status = Column(String)
    issued_at = Column(DateTime, default=datetime.utcnow)
    pdf_path = Column(String)
    version = Column(Integer, default=1)


class Partner(Base):
    __tablename__ = "partners"
    id = Column(Integer, primary_key=True, autoincrement=True)
    partner_id = Column(String, unique=True, index=True)
    partner_name = Column(String)
    address = Column(Text)
    tva_number = Column(String)
    tier = Column(Integer)
    payment_terms_days = Column(Integer, default=30)
    discount_rate = Column(Float, default=0.0)


class GradesRegulationCache(Base):
    __tablename__ = "grades_regulation_cache"
    id = Column(Integer, primary_key=True, autoincrement=True)
    grade = Column(String(5), nullable=False, index=True)
    eu_grade_label = Column(String(50))
    eu_criteria_summary = Column(Text)
    destination = Column(String(50))
    destination_options = Column(JSON)
    innorpi_aligned = Column(Boolean)
    innorpi_note = Column(Text, nullable=True)
    market_price_TND = Column(Float, nullable=True)
    price_source_url = Column(String(500), nullable=True)
    regulatory_source = Column(String(200))
    mapping_confidence = Column(String(10))
    search_date = Column(DateTime)
    expires_at = Column(DateTime, index=True)
    cache_hit_count = Column(Integer, default=0)


# ── TABLE NOTIFICATIONS ───────────────────────────────────────
# Schéma unifié : compatible avec notification_service.py
# Remplace l'ancien modèle Notification qui avait un schéma différent.
class Notification(Base):
    __tablename__ = "notifications"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    partner     = Column(String(100), nullable=True, index=True)
    event_type  = Column(String(50), nullable=True)   # egg_allocated | order_fulfilled | stock_shortage
    order_id    = Column(String(50), nullable=True)
    lot_id      = Column(String(100), nullable=True, index=True)
    grade       = Column(String(10), nullable=True)
    message     = Column(Text, nullable=False)
    payload     = Column(Text, nullable=True)          # JSON stringifié
    created_at  = Column(DateTime, default=datetime.utcnow)
    delivered   = Column(Integer, default=0)           # 1 = simulé comme envoyé

    # Champs de l'ancien modèle — gardés pour compatibilité frontend
    title       = Column(String(200), nullable=True)
    severity    = Column(String(20), default="info")   # info | warning | critical | success
    tool_source = Column(String(100), nullable=True)
    is_read     = Column(Boolean, default=False)