# backend/tools/output_tools.py  — Correction colonnes DB manquantes
import os
import json
import logging
from datetime import datetime
from typing import Optional
from langchain_core.tools import tool
from backend.db.database import SessionLocal
from backend.db.models import Alert, Invoice, Partner

logger = logging.getLogger(__name__)

REJECTED_GRADES = {"E", "D", "Rejected", "Oeuf industriel", "UNGRADED"}

try:
    from backend.tools.rag_tools import client, sentence_transformer_ef
    memory_collection = client.get_or_create_collection(
        name="egg_memory", embedding_function=sentence_transformer_ef
    ) if client else None
except Exception:
    memory_collection = None


# ─────────────────────────────────────────────────────────────
# alert_and_logger — utilise SQL brut pour éviter partner_id manquant
# ─────────────────────────────────────────────────────────────
@tool
async def alert_and_logger(
    lot_id: str,
    grade: Optional[str] = "E",
    quality_result: Optional[dict] = None,
    fertility_result: Optional[dict] = None,
    vlm_result: Optional[dict] = None,
    crack_detected: Optional[bool] = False,
    crack_severity: Optional[str] = "none",
    blood_spot_detected: Optional[bool] = False,
    shell_condition: Optional[str] = "unknown",
    freshness_estimate: Optional[str] = "unknown",
    size_class: Optional[str] = "M",
    destination: Optional[str] = "unknown",
    lot_quantity: Optional[int] = 1,
    farm_zone: Optional[str] = "unknown",
    lay_date: Optional[str] = None,
    needs_human_review: Optional[bool] = False,
    rejection_reason: Optional[str] = None,
    market_price_TND: Optional[float] = None,
    confidence: Optional[float] = 0.0,
    reasoning: Optional[str] = "No reasoning provided",
    weight_g: Optional[float] = None,
    defects_detected: Optional[str] = None,
    grading_reasoning: Optional[str] = None,
    shell_assessment: Optional[str] = None,
    internal_assessment: Optional[str] = None,
    web_search_sources_used: Optional[list] = None,
    compliance_notes: Optional[str] = None,
    regulatory_basis: Optional[str] = None,
    blood_spots_in_lot: Optional[int] = 0,
    large_air_cells_in_lot: Optional[int] = 0,
    order_unfulfillable_48h: Optional[bool] = False,
    invoice_failed: Optional[bool] = False
) -> dict:
    """Log the analyzed egg lot and trigger smart alerts."""
    alerts_generated = []
    rejection_rate   = 0.0
    total_today      = 0

    grade            = grade or "UNGRADED"
    quality_result   = quality_result   if isinstance(quality_result,   dict) else {}
    fertility_result = fertility_result if isinstance(fertility_result, dict) else {}
    vlm_result       = vlm_result       if isinstance(vlm_result,       dict) else {}

    quality_str   = quality_result.get("quality",           "unknown")
    fertility_str = fertility_result.get("fertility_status","unknown")

    if isinstance(defects_detected, list):
        defects_detected = json.dumps(defects_detected)
    elif defects_detected is None:
        defects_detected = "[]"

    db = SessionLocal()
    try:
        from sqlalchemy import text

        # Étape 1 : INSERT OR IGNORE
        db.execute(
            text("""INSERT OR IGNORE INTO lots
                    (lot_id, grade, quality, fertility_status, confidence, timestamp)
                    VALUES (:lid, :grade, 'unknown', 'unknown', 0.0, :ts)"""),
            {"lid": lot_id, "grade": grade, "ts": datetime.utcnow().isoformat()}
        )
        db.commit()

        # Étape 2 : UPDATE
        db.execute(
            text("""UPDATE lots SET
                    grade=:grade, quality=:quality, fertility_status=:fs,
                    confidence=:conf, reasoning_trace=:rt, timestamp=:ts,
                    size_class=:sc, weight_g=:wg, destination=:dest,
                    defects_detected=:dd, grading_reasoning=:gr,
                    shell_assessment=:sa, internal_assessment=:ia,
                    needs_human_review=:nhr
                    WHERE lot_id=:lot_id"""),
            {
                "lot_id": lot_id, "grade": grade,
                "quality": quality_str, "fs": fertility_str,
                "conf": confidence or 0.0, "rt": reasoning or "",
                "ts": datetime.utcnow(), "sc": size_class or "M",
                "wg": weight_g, "dest": destination or "unknown",
                "dd": defects_detected, "gr": grading_reasoning or "",
                "sa": shell_assessment or "", "ia": internal_assessment or "",
                "nhr": int(bool(needs_human_review))
            }
        )
        db.commit()

        # Calcul taux de rejet
        rows = db.execute(
            text("SELECT grade FROM lots WHERE timestamp >= :ts"),
            {"ts": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
        ).fetchall()
        total_today    = len(rows)
        rejected_count = sum(1 for r in rows if r[0] in REJECTED_GRADES)
        rejection_rate = rejected_count / total_today if total_today > 0 else 0.0

        # Alertes DB
        def _add_alert(msg: str, rate: float = 0.0):
            try:
                db.execute(
                    text("INSERT INTO alerts (lot_id, rejection_rate, message, timestamp) VALUES (:lid, :rate, :msg, :ts)"),
                    {"lid": lot_id, "rate": rate, "msg": msg, "ts": datetime.utcnow()}
                )
            except Exception as ae:
                logger.warning(f"[alert] Insert failed: {ae}")

        if rejection_rate > 0.05:
            _add_alert(f"[WARNING] High rejection rate: {rejection_rate*100:.1f}%", rejection_rate)
            alerts_generated.append(f"High rejection rate: {rejection_rate*100:.1f}%")

        if blood_spots_in_lot and blood_spots_in_lot >= 3:
            _add_alert("[CRITICAL] 3+ blood spots — possible flock disease")
            alerts_generated.append("Blood spots critical")

        if large_air_cells_in_lot and large_air_cells_in_lot >= 5:
            _add_alert("[WARNING] 5+ large air cells — cold chain failure")
            alerts_generated.append("Cold chain failure")

        if order_unfulfillable_48h:
            _add_alert("[WARNING] Partner order unfulfillable within 48h")
            alerts_generated.append("Order unfulfillable")

        if invoice_failed:
            _add_alert("[ERROR] Invoice generation failed")
            alerts_generated.append("Invoice failure")

        db.commit()

    except Exception as e:
        logger.error(f"[alert_and_logger] DB error: {e}", exc_info=True)
        try: db.rollback()
        except Exception: pass
        return {"logged": False, "lot_id": lot_id, "error": str(e),
                "alerts_generated": [], "rejection_rate": 0.0}
    finally:
        db.close()

    # ChromaDB memory (best-effort)
    if memory_collection:
        try:
            memory_collection.add(
                documents=[f"Lot {lot_id}: Grade {grade}, Q={quality_str}. {reasoning}"],
                ids=[f"mem_{lot_id}_{datetime.utcnow().timestamp()}"]
            )
        except Exception:
            pass

    # ── Email technicien — UNIQUEMENT si rejet physique ───────
    # Un rejet physique = coquille cassée OU fissure visible/structurelle
    # PAS basé sur le grade CNN — basé sur le défaut VLM détecté
    rejets_physiques = []

    # Vérifier depuis les paramètres directs
    if crack_detected and crack_severity in ["visible", "structural"]:
        rejets_physiques.append("fissure structurelle")
    if (shell_condition or "") == "broken":
        rejets_physiques.append("coquille cassée")

    # Vérifier aussi dans vlm_result (plus fiable)
    vlm_crack    = vlm_result.get("crack_detected", False)
    vlm_crack_sev= vlm_result.get("crack_severity", "none") or "none"
    vlm_shell    = vlm_result.get("shell_condition", "") or ""

    if vlm_crack and vlm_crack_sev in ["visible", "structural"]:
        if "fissure structurelle" not in rejets_physiques:
            rejets_physiques.append("fissure structurelle")
    if vlm_shell == "broken":
        if "coquille cassée" not in rejets_physiques:
            rejets_physiques.append("coquille cassée")

    if rejets_physiques:
        try:
            from backend.services.technician_mailer import send_lot_validated
            import threading
            threading.Thread(target=send_lot_validated, kwargs={
                "lot_id":          lot_id,
                "grade":           grade,
                "destination":     "Rejection / Destruction",
                "confidence":      confidence or 0.0,
                "grading_source":  f"Rejet physique : {', '.join(rejets_physiques)}",
                "defects":         rejets_physiques,
                "partner_name":    None,
                "allocation_notes":None,
                "needs_review":    True
            }, daemon=True).start()
            logger.info(f"[alert_and_logger] ✅ Email rejet physique envoyé pour {lot_id}: {rejets_physiques}")
        except Exception as mail_err:
            logger.warning(f"[mailer] Non-bloquant: {mail_err}")
    else:
        logger.info(f"[alert_and_logger] Pas de rejet physique pour {lot_id} (grade={grade}) — pas d'email")

    return {
        "logged": True, "lot_id": lot_id, "grade_stored": grade,
        "alerts_generated": alerts_generated,
        "rejection_rate": round(rejection_rate, 4),
        "total_analyzed_today": total_today
    }

# ─────────────────────────────────────────────────────────────
# report_and_qr_generator
# ─────────────────────────────────────────────────────────────
@tool
async def report_and_qr_generator(
    lot_id: str,
    grade: Optional[str] = None,
    destination: Optional[str] = None,
    grader_result: Optional[dict] = None,
    total_inspected: Optional[int] = 1,
    class_a_count: Optional[int] = 0,
    class_b_count: Optional[int] = 0,
    industrial_count: Optional[int] = 0,
    size_distribution: Optional[dict] = None,
    avg_quality_score: Optional[float] = 0.0,
    defect_breakdown: Optional[dict] = None,
    regulatory_statement: Optional[str] = "Inspected per EU 2023/2465 + INNORPI",
    market_price_tnd: Optional[float] = None,
    estimated_lot_value_tnd: Optional[float] = None,
    alerts_generated: Optional[list] = None
) -> dict:
    """Generate PDF report + QR code. Always returns generated_id = lot_id."""
    grader_result    = grader_result    if isinstance(grader_result, dict) else {}
    grade            = grade            or grader_result.get("final_grade") or "UNGRADED"
    destination      = destination      or grader_result.get("destination") or "Unknown"
    market_price_tnd = market_price_tnd or grader_result.get("market_price_TND")
    alerts_generated = alerts_generated if isinstance(alerts_generated, list) else []
    size_distribution= size_distribution if isinstance(size_distribution, dict) else {}
    total_inspected  = total_inspected or 1

    pdf_ok  = False
    pdf_path= None
    qr_path = None
    qr_url  = f"http://localhost:8000/lot/{lot_id}/audit"

    try:
        reports_dir = os.path.join("backend", "reports")
        os.makedirs(reports_dir, exist_ok=True)
        pdf_path = os.path.join(reports_dir, f"{lot_id}_report.pdf")
        qr_path  = os.path.join(reports_dir, f"{lot_id}_qr.png")

        # QR Code
        try:
            import qrcode as qrcode_lib
            qrcode_lib.make(qr_url).save(qr_path)
        except Exception as qe:
            logger.warning(f"[report] QR failed: {qe}"); qr_path = None

        # PDF
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            c = canvas.Canvas(pdf_path, pagesize=letter)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(72, 750, f"Rapport Qualité — Lot: {lot_id}")
            c.setFont("Helvetica", 11)
            c.drawString(72, 732, f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
            c.drawString(72, 714, f"Grade: {grade}  |  Destination: {destination}")
            rejection_rate = (industrial_count / total_inspected * 100) if total_inspected > 0 else 0
            c.drawString(72, 690, f"Total: {total_inspected}  |  Rejet: {rejection_rate:.1f}%")
            price_str = f"{market_price_tnd:.3f}" if market_price_tnd else "N/A"
            c.drawString(72, 670, f"Prix marché: {price_str} TND/œuf")
            y = 640
            c.drawString(72, y, "Recommandations:")
            for alert in (alerts_generated or ["Lot conforme — traitement standard"]):
                y -= 18; c.drawString(90, y, f"— {alert}")
            c.setFont("Helvetica-Oblique", 9)
            c.drawString(72, y-30, regulatory_statement)
            c.drawString(72, y-44, "El Mazraa — Groupe Poulina")
            if qr_path and os.path.exists(qr_path):
                try: c.drawImage(qr_path, 420, 650, width=120, height=120)
                except Exception: pass
            c.save(); pdf_ok = True
        except Exception as pe:
            logger.error(f"[report] PDF failed: {pe}", exc_info=True)

        # Mettre à jour qr_code_path si la colonne existe
        if pdf_ok and qr_path:
            try:
                db = SessionLocal()
                from sqlalchemy import text
                db.execute(text("UPDATE lots SET qr_code_path=:qr WHERE lot_id=:lid"),
                           {"qr": qr_path, "lid": lot_id})
                db.commit(); db.close()
            except Exception: pass

    except Exception as e:
        logger.error(f"[report_and_qr_generator] Outer error: {e}", exc_info=True)
        return {"success": False, "generated_id": lot_id, "lot_id": lot_id,
                "grade": grade, "error": str(e)}

    return {
        "success":      pdf_ok,
        "generated_id": lot_id,
        "lot_id":       lot_id,
        "pdf_path":     pdf_path if pdf_ok else None,
        "qr_path":      qr_path,
        "qr_url":       qr_url,
        "grade":        grade,
        "destination":  destination
    }


# ─────────────────────────────────────────────────────────────
# invoice_generator
# ─────────────────────────────────────────────────────────────
@tool
async def invoice_generator(
    partner_id: str, order_id: str, lot_id: str,
    items: list, market_price_tnd: float
) -> dict:
    """Generate a formal PDF invoice for an allocated order lot."""
    db = SessionLocal()
    try:
        partner = db.query(Partner).filter(Partner.partner_id == partner_id).first()
        discount_rate = partner.discount_rate if partner else 0.0
        partner_name  = partner.partner_name  if partner else "Unknown"
        address       = partner.address       if partner else ""

        from sqlalchemy import text
        row = db.execute(text("SELECT COUNT(*) FROM invoices")).fetchone()
        seq = (row[0] if row else 0) + 1
        invoice_id = f"INV-{datetime.utcnow().year}-{seq:04d}"

        invoices_dir = os.path.join("backend", "invoices")
        os.makedirs(invoices_dir, exist_ok=True)
        pdf_path = os.path.join(invoices_dir, f"{invoice_id}_{partner_id}.pdf")

        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as rl_canvas
        c = rl_canvas.Canvas(pdf_path, pagesize=letter)
        c.drawString(50, 750, f"Groupe Poulina — Facture N°: {invoice_id}")
        c.drawString(50, 730, f"Client: {partner_name}  |  Lot: {lot_id}")
        y = 680; subtotal = 0.0
        for item in (items or []):
            qty = item.get("qty", 0)
            unit_p = market_price_tnd * (1 - discount_rate)
            total_line = qty * unit_p; subtotal += total_line
            c.drawString(50, y, f"{item.get('grade')} | {item.get('size')} | {qty} | {unit_p:.3f} | {total_line:.3f} TND")
            y -= 20
        tva = subtotal * 0.19; total = subtotal + tva
        c.drawString(300, y-20, f"HT: {subtotal:.3f}  TVA 19%: {tva:.3f}  TTC: {total:.3f} TND")
        c.save()

        db.execute(
            text("""INSERT INTO invoices (invoice_id, partner_id, lot_id, order_id, total_HT, tva_amount, total_TTC, status, pdf_path, issued_at)
                    VALUES (:iid, :pid, :lid, :oid, :ht, :tva, :ttc, 'issued', :pdf, :ts)"""),
            {"iid": invoice_id, "pid": partner_id, "lid": lot_id, "oid": order_id,
             "ht": subtotal, "tva": tva, "ttc": total, "pdf": pdf_path, "ts": datetime.utcnow()}
        )
        db.commit()
        return {"invoice_id": invoice_id, "pdf_path": pdf_path, "total_TTC": total, "status": "issued"}
    except Exception as e:
        db.rollback(); logger.error(f"[invoice] Error: {e}", exc_info=True)
        return {"error": str(e), "status": "failed"}
    finally:
        db.close()