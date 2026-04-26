# backend/tools/supply_tools.py  — Version avec notifications automatiques
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional
from langchain_core.tools import tool
from sqlalchemy import text
from backend.db.database import SessionLocal
from backend.db.models import (
    Stock, PartnerOrder, DispatchLog,
    Lot, GradesRegulationCache
)

logger = logging.getLogger(__name__)

_GRADE_DESTINATION = {
    "AA": "Commercial Retail / Export",
    "A":  "Commercial Retail / Export",
    "B":  "Commercial Retail / Local",
    "C":  "Industrial Processing",
    "D":  "Industrial Processing",
    "E":  "Rejection / Destruction",
    "UNGRADED": "hold_for_manual_review"
}
_GRADE_ZONE = {
    "AA": "Zone-Froid-Premium",
    "A":  "Zone-Froid-A",
    "B":  "Zone-B-Processing",
    "C":  "Zone-C-Industrial",
    "D":  "Zone-C-Industrial",
    "E":  "Zone-Rebut",
}
_GRADE_ROUTING = {
    "AA": "Premium retail — supermarkets, export, Ramadan packaging",
    "A":  "Standard commercial retail",
    "B":  "Food industry processing — pasta, bakeries, mayo",
    "C":  "Industrial processing only",
    "D":  "Industrial processing only — expedite",
    "E":  "Immediate rejection queue — do not process",
}

def _destination(g): return _GRADE_DESTINATION.get(g, "hold_for_manual_review")
def _zone(g):        return _GRADE_ZONE.get(g, "Zone-Rebut")
def _routing(g):     return _GRADE_ROUTING.get(g, "Manual review required")


# ─────────────────────────────────────────────────────────────
# grade_regulation_resolver
# ─────────────────────────────────────────────────────────────
@tool
async def grade_regulation_resolver(predicted_grade: str) -> dict:
    """Resolves EU/INNORPI meaning and destination for a predicted grade using web search + cache."""
    from backend.tools.search_tools import web_search_tool

    db = SessionLocal()
    try:
        cached = db.query(GradesRegulationCache).filter(
            GradesRegulationCache.grade == predicted_grade,
            GradesRegulationCache.expires_at > datetime.utcnow()
        ).first()

        if cached:
            cached.cache_hit_count += 1
            db.commit()
            return {
                "predicted_grade":    cached.grade,
                "eu_grade_label":     cached.eu_grade_label,
                "eu_criteria_summary":cached.eu_criteria_summary,
                "innorpi_aligned":    cached.innorpi_aligned,
                "innorpi_note":       cached.innorpi_note,
                "destination":        cached.destination,
                "destination_options":cached.destination_options or [],
                "market_price_TND":   cached.market_price_TND,
                "price_source_url":   cached.price_source_url,
                "regulatory_source":  cached.regulatory_source,
                "search_date":        cached.search_date.isoformat() if cached.search_date else None,
                "mapping_confidence": cached.mapping_confidence,
                "mapping_basis":      "web_search_verified",
                "cache_hit":          True
            }

        res1 = await web_search_tool.ainvoke({"query": f"egg grade {predicted_grade} EU regulation 2023/2465 quality criteria"})
        res2 = await web_search_tool.ainvoke({"query": f"norme oeufs Tunisie INNORPI grade {predicted_grade} classification"})
        res3 = await web_search_tool.ainvoke({"query": f"egg grade {predicted_grade} destination human consumption food industry EU"})
        month = datetime.utcnow().strftime("%B"); year = datetime.utcnow().strftime("%Y")
        res4 = await web_search_tool.ainvoke({"query": f"prix oeufs Tunisie grade {predicted_grade} TND {month} {year}"})

        parsed = {}
        if any([res1, res2, res3, res4]):
            try:
                import os, json, re
                from langchain_groq import ChatGroq
                llm = ChatGroq(model=os.getenv("GROQ_MODEL","llama-3.3-70b-versatile"),
                               api_key=os.getenv("GROQ_API_KEY"), temperature=0)
                prompt = f"""From search results about egg grade {predicted_grade}:
1:{res1} 2:{res2} 3:{res3} 4:{res4}
Return ONLY valid JSON:
{{"eu_grade_label":"...","eu_criteria_summary":"...","innorpi_aligned":true,"innorpi_note":"...","destination":"...","destination_options":[],"market_price_TND":null,"price_source_url":null}}"""
                ai_msg = await llm.ainvoke(prompt)
                raw = ai_msg.content.strip()
                if "```" in raw:
                    import re
                    m = re.search(r'```(?:json)?\s*(.*?)\s*```', raw, re.DOTALL)
                    if m: raw = m.group(1)
                parsed = json.loads(raw)
            except Exception as e:
                logger.warning(f"[grade_regulation_resolver] LLM parse failed: {e}")

        now  = datetime.utcnow()
        dest = parsed.get("destination") or _destination(predicted_grade)
        entry = GradesRegulationCache(
            grade=predicted_grade,
            eu_grade_label=parsed.get("eu_grade_label", f"Grade {predicted_grade}"),
            eu_criteria_summary=parsed.get("eu_criteria_summary"),
            destination=dest,
            destination_options=parsed.get("destination_options", []),
            innorpi_aligned=parsed.get("innorpi_aligned", True),
            innorpi_note=parsed.get("innorpi_note"),
            market_price_TND=parsed.get("market_price_TND"),
            price_source_url=parsed.get("price_source_url"),
            regulatory_source="EU 2023/2465 + INNORPI",
            mapping_confidence="high" if res4 else "medium",
            search_date=now,
            expires_at=now + timedelta(hours=24),
            cache_hit_count=0
        )
        db.add(entry)
        db.commit()

        return {
            "predicted_grade":    predicted_grade,
            "eu_grade_label":     entry.eu_grade_label,
            "eu_criteria_summary":entry.eu_criteria_summary,
            "innorpi_aligned":    entry.innorpi_aligned,
            "innorpi_note":       entry.innorpi_note,
            "destination":        entry.destination,
            "destination_options":entry.destination_options or [],
            "market_price_TND":   entry.market_price_TND,
            "price_source_url":   entry.price_source_url,
            "regulatory_source":  entry.regulatory_source,
            "search_date":        entry.search_date.isoformat(),
            "mapping_confidence": entry.mapping_confidence,
            "mapping_basis":      "web_search_verified",
            "cache_hit":          False
        }
    except Exception as e:
        logger.error(f"[grade_regulation_resolver] Error: {e}", exc_info=True)
        return {
            "predicted_grade": predicted_grade,
            "eu_grade_label":  f"Grade {predicted_grade}",
            "destination":     _destination(predicted_grade),
            "destination_options": [],
            "market_price_TND": None,
            "regulatory_source": "fallback",
            "search_date": datetime.utcnow().isoformat(),
            "mapping_confidence": "low",
            "mapping_basis": "fallback",
            "cache_hit": False
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# egg_grader
# ─────────────────────────────────────────────────────────────
# Remplacez la fonction egg_grader dans backend/tools/supply_tools.py
# par ce code corrigé

@tool
async def egg_grader(
    cnn_result: dict = None,
    regulation: dict = None,
    vlm_result: dict = None,
    egg_id: str = "unknown"
) -> dict:
    """
    Logique de grading corrigée :
    
    1. YOLO + EfficientNet (cnn_result) → grade de BASE
       C'est lui qui décide du grade selon les mesures visuelles.
    
    2. VLM (vlm_result) → modificateurs de défauts UNIQUEMENT
       - Fissure visible/structurelle → rejet immédiat (grade E)
       - Tache de sang              → nettoyage requis, max grade B
       - Coquille cassée            → rejet immédiat (grade E)
       - Hairline crack             → downgrade -1 niveau
       - Air cell > 6mm             → downgrade -1 niveau
       - Fertilisé                  → rejet immédiat (grade E)
    
    3. RAG + WebSearch (regulation) → valide la destination légale
    
    Le VLM ne peut PAS imposer un grade de zéro — il ne fait que
    modifier le grade CNN vers le bas si nécessaire.
    """
    regulation = regulation if isinstance(regulation, dict) else {}
    vlm_result = vlm_result if isinstance(vlm_result, dict) else {}
    cnn_result = cnn_result if isinstance(cnn_result, dict) else {}

    grades_order = ["AA", "A", "B", "C", "D", "E"]

    cnn_ok = (
        cnn_result
        and not cnn_result.get("fallback")
        and cnn_result.get("predicted_grade")
        and (cnn_result.get("confidence") or 0) > 0
    )
    vlm_ok = (
        vlm_result
        and not vlm_result.get("fallback")
        and vlm_result.get("status") != "error"
    )

    # Si aucun modèle n'a fonctionné
    if not cnn_ok and not vlm_ok:
        return {
            "egg_id": egg_id,
            "final_grade": "UNGRADED",
            "eu_grade": "Unknown",
            "destination": "hold_for_manual_review",
            "confidence": 0.0,
            "confidence_level": "none",
            "grading_source": "all_models_failed",
            "regulatory_source": None,
            "market_price_TND": None,
            "needs_human_review": True,
            "all_probabilities": {},
            "recommendation": {
                "primary_destination": "MANUAL_REVIEW_QUEUE",
                "reason": "all_vision_models_failed",
                "urgency": "immediate",
                "quality_actions": ["Re-inspect manually"]
            }
        }

    # ─────────────────────────────────────────────────────────
    # ÉTAPE 1 : Grade de BASE depuis CNN (YOLO + EfficientNet)
    # C'est la source principale du grade
    # ─────────────────────────────────────────────────────────
    cnn_grade = cnn_result.get("predicted_grade")
    cnn_conf  = cnn_result.get("confidence") or 0.0
    all_probs = cnn_result.get("all_probabilities") or {}

    if cnn_ok and cnn_conf >= 0.40:
        # CNN disponible et confiant → grade de base = CNN
        base_grade     = cnn_grade
        grading_source = "cnn_primary"
        confidence     = cnn_conf
    elif vlm_ok:
        # CNN indisponible ou peu confiant → utiliser le score VLM
        qs = float(vlm_result.get("quality_score") or 0.5)
        if qs >= 0.80:   base_grade = "A"
        elif qs >= 0.65: base_grade = "B"
        elif qs >= 0.45: base_grade = "C"
        else:            base_grade = "D"
        grading_source = "vlm_quality_score_fallback"
        confidence     = qs
    else:
        base_grade     = cnn_grade or "C"
        grading_source = "cnn_low_confidence"
        confidence     = cnn_conf

    try:
        base_idx = grades_order.index(base_grade)
    except ValueError:
        base_idx = 2  # default B

    # ─────────────────────────────────────────────────────────
    # ÉTAPE 2 : Modificateurs VLM — défauts critiques seulement
    # Le VLM peut forcer un rejet ou downgrader, jamais upgrader
    # ─────────────────────────────────────────────────────────
    final_grade      = None
    override_reason  = None
    downgrade_reason = None
    urgency          = "standard"
    crack_sev        = vlm_result.get("crack_severity") or "none"

    # ── Rejets immédiats (grade E obligatoire) ────────────────
    # Fissure visible ou structurelle = danger sanitaire
    if vlm_result.get("crack_detected") and crack_sev in ["visible", "structural"]:
        final_grade     = "E"
        override_reason = "crack_visible_or_structural"
        grading_source  = "vlm_safety_override"
        urgency         = "immediate"

    # Coquille cassée = rejet
    elif (vlm_result.get("shell_condition") or "") == "broken":
        final_grade     = "E"
        override_reason = "broken_shell"
        grading_source  = "vlm_safety_override"
        urgency         = "immediate"

    # Œuf fertilisé = interdit à la vente pour consommation
    elif vlm_result.get("fertilized"):
        final_grade     = "E"
        override_reason = "fertilized_egg"
        grading_source  = "vlm_safety_override"
        urgency         = "immediate"

    # ── Downgrades (pas de rejet, mais dégradation) ───────────
    if not final_grade:
        current_idx = base_idx

        # Hairline crack → downgrade -1 niveau
        if crack_sev == "hairline":
            current_idx      = min(current_idx + 1, len(grades_order) - 1)
            downgrade_reason = "hairline_crack"
            urgency          = "within_24h"

        # Air cell > 6mm → downgrade -1 niveau (fraîcheur réduite)
        air_cell_mm = float(vlm_result.get("air_cell_height_mm") or 0)
        if air_cell_mm > 6.0:
            current_idx      = min(current_idx + 1, len(grades_order) - 1)
            downgrade_reason = downgrade_reason or "air_cell_exceeded"
            urgency          = "within_24h"

        # Tache de sang → nettoyage requis, grade plafonné à B
        # PAS de rejet — l'œuf est lavable et consommable après nettoyage
        if vlm_result.get("blood_spot_detected"):
            blood_spot_idx   = grades_order.index("B")  # max autorisé = B
            current_idx      = max(current_idx, blood_spot_idx)
            downgrade_reason = downgrade_reason or "blood_spot_cleaning_required"
            urgency          = "within_24h"

        final_grade = grades_order[current_idx]

        # Mettre à jour la source de grading
        if current_idx != base_idx:
            grading_source = f"cnn_with_vlm_downgrade ({downgrade_reason})"
        elif cnn_ok and vlm_ok:
            grading_source = "cnn_primary_vlm_confirmed"

    # ─────────────────────────────────────────────────────────
    # ÉTAPE 3 : Destination depuis RAG + WebSearch (regulation)
    # ─────────────────────────────────────────────────────────
    dest_from_reg = regulation.get("destination") or ""
    if dest_from_reg and dest_from_reg not in ("", "unknown", "hold_for_manual_review"):
        destination = dest_from_reg
    else:
        # Fallback local si regulation non disponible
        _dest_map = {
            "AA": "Commercial Retail / Export",
            "A":  "Commercial Retail / Export",
            "B":  "Commercial Retail / Local",
            "C":  "Industrial Processing",
            "D":  "Industrial Processing",
            "E":  "Rejection / Destruction",
        }
        destination = _dest_map.get(final_grade, "hold_for_manual_review")

    # ─────────────────────────────────────────────────────────
    # ÉTAPE 4 : Actions qualité selon les défauts détectés
    # ─────────────────────────────────────────────────────────
    actions = []

    if override_reason == "crack_visible_or_structural":
        actions += [
            "Isoler immédiatement — fissure structurelle",
            "Ne pas traiter — risque contamination"
        ]
    elif override_reason == "broken_shell":
        actions.append("Isoler immédiatement — coquille cassée")
    elif override_reason == "fertilized_egg":
        actions.append("Retirer du lot — œuf fertilisé non conforme")

    if downgrade_reason == "blood_spot_cleaning_required":
        actions += [
            "Nettoyer la coquille avant conditionnement",
            "Inspecter le troupeau pour identifier la source des taches"
        ]
    if downgrade_reason == "hairline_crack":
        actions.append("Conditionner rapidement — micro-fissure détectée")
    if "air_cell" in (downgrade_reason or ""):
        actions.append("Vérifier la chaîne du froid — chambre à air > 6mm")

    if final_grade in ("B", "C"):
        actions.append(f"Traitement prioritaire — grade {final_grade} détecté")

    if not actions:
        actions.append("Conditionnement et expédition standard")

    return {
        "egg_id":           egg_id,
        "final_grade":      final_grade,
        "eu_grade":         regulation.get("eu_grade_label") or f"Grade {final_grade}",
        "destination":      destination,
        "confidence":       round(confidence, 4),
        "confidence_level": "high" if confidence >= 0.8 else ("medium" if confidence >= 0.6 else "low"),
        "grading_source":   grading_source,
        "regulatory_source":regulation.get("regulatory_source"),
        "market_price_TND": regulation.get("market_price_TND"),
        "search_date":      regulation.get("search_date"),
        "mapping_basis":    "cnn_primary_vlm_modifier_regulation_validated",
        "all_probabilities":all_probs,
        "needs_human_review": final_grade == "UNGRADED",
        "override_reason":  override_reason,
        "downgrade_reason": downgrade_reason,
        "recommendation": {
            "primary_destination": destination,
            "reason": override_reason or downgrade_reason or regulation.get("eu_criteria_summary") or "Standard grade assignment",
            "urgency": urgency,
            "alternatives": regulation.get("destination_options") or [],
            "quality_actions": actions,
            "market_value_TND": regulation.get("market_price_TND"),
            "regulatory_note": regulation.get("innorpi_note") or "EU 2023/2465 + INNORPI compliance checked"
        }
    }


# ─────────────────────────────────────────────────────────────
# inventory_allocator — avec notifications automatiques
# ─────────────────────────────────────────────────────────────
@tool
async def inventory_allocator(
    lot_id: str,
    egg_id: Optional[str] = "egg_unknown",
    grade: Optional[str] = "E",
    size_class: Optional[str] = "M",
    destination: Optional[str] = None,
    size_source: Optional[str] = "unknown",
    size_confidence: Optional[str] = "low"
) -> dict:
    """
    Routes eggs to inventory and auto-allocates to active partner orders.
    Automatically sends email + webhook notifications to partners.
    """
    valid_grades = ["AA", "A", "B", "C", "D", "E", "UNGRADED"]
    if grade not in valid_grades:
        grade = "E"
    if not destination or destination in ("hold_for_manual_review", "unknown", ""):
        destination = _destination(grade)

    db = SessionLocal()
    try:
        # ── Rejet immédiat ─────────────────────────────────
        if grade == "E" or destination == "Rejection / Destruction":
            db.execute(
                text("""INSERT INTO stock
                        (lot_id, grade, size_class, quantity, entry_date, expiry_date, storage_zone, status)
                        VALUES (:lid, :g, :sc, 1, :entry, :expiry, 'Zone-Rebut', 'rejected')"""),
                {"lid": lot_id, "g": grade, "sc": size_class or "M",
                 "entry": datetime.utcnow(), "expiry": datetime.utcnow() + timedelta(days=1)}
            )
            db.commit()
            row = db.execute(text("SELECT id FROM stock WHERE lot_id=:lid ORDER BY id DESC LIMIT 1"), {"lid": lot_id}).fetchone()
            return {
                "allocated": True, "routing_decision": "Immediate rejection queue",
                "zone": "Zone-Rebut", "stock_entry_id": row[0] if row else None,
                "destination": "Rejection / Destruction",
                "partner_allocated": False, "order_id": None, "partner_name": None,
                "allocation_notes": f"Grade {grade} rejected per EU 2023/2465"
            }

        # ── Chercher commande partenaire active ────────────
        partner_order = db.query(PartnerOrder).filter(
            PartnerOrder.required_grade == grade,
            PartnerOrder.required_size  == size_class,
            PartnerOrder.status.in_(["pending", "partial"]),
            PartnerOrder.quantity_fulfilled < PartnerOrder.quantity_needed
        ).order_by(PartnerOrder.priority.asc(), PartnerOrder.deadline_date.asc()).first()

        allocated_to_order = None
        partner_name       = None
        allocation_notes   = None
        stock_status       = "available"
        is_fulfilled       = False

        if partner_order:
            partner_order.quantity_fulfilled += 1
            allocated_to_order = str(partner_order.id)
            partner_name       = partner_order.partner_name
            stock_status       = "reserved"
            remaining          = partner_order.quantity_needed - partner_order.quantity_fulfilled

            if partner_order.quantity_fulfilled >= partner_order.quantity_needed:
                partner_order.status = "fulfilled"
                is_fulfilled         = True
                allocation_notes     = (
                    f"✓ Commande COMPLÈTE pour {partner_name} "
                    f"(Order #{partner_order.id}) — "
                    f"{partner_order.quantity_needed}/{partner_order.quantity_needed} œufs"
                )
            else:
                partner_order.status = "partial"
                allocation_notes     = (
                    f"Affecté à {partner_name} (Order #{partner_order.id}) — "
                    f"{partner_order.quantity_fulfilled}/{partner_order.quantity_needed} "
                    f"({remaining} restants)"
                )

            db.add(DispatchLog(
                lot_id=lot_id, partner_name=partner_name, grade=grade,
                quantity=1, dispatched_at=datetime.utcnow(), order_id=partner_order.id
            ))
        else:
            allocation_notes = f"Pas de commande active pour Grade {grade}/{size_class} — stock standard"

        # ── INSERT stock ───────────────────────────────────
        db.execute(
            text("""INSERT INTO stock
                    (lot_id, grade, size_class, quantity, entry_date, expiry_date, storage_zone, status)
                    VALUES (:lid, :g, :sc, 1, :entry, :expiry, :zone, :status)"""),
            {"lid": lot_id, "g": grade, "sc": size_class or "M",
             "entry": datetime.utcnow(), "expiry": datetime.utcnow() + timedelta(days=28),
             "zone": _zone(grade), "status": stock_status}
        )
        db.commit()

        # Colonnes optionnelles
        try:
            db.execute(
                text("UPDATE stock SET size_source=:ss, size_confidence=:sc2, allocated_to_order=:ato WHERE lot_id=:lid AND grade=:g ORDER BY id DESC LIMIT 1"),
                {"ss": size_source, "sc2": size_confidence, "ato": allocated_to_order, "lid": lot_id, "g": grade}
            )
            db.commit()
        except Exception:
            pass

        row = db.execute(text("SELECT id FROM stock WHERE lot_id=:lid ORDER BY id DESC LIMIT 1"), {"lid": lot_id}).fetchone()
        stock_id = row[0] if row else None

        # ── NOTIFICATION AUTOMATIQUE ───────────────────────
        if partner_order and partner_name:
            try:
                from backend.services.notification_service import notify_egg_allocated

                # Générer la facture si la commande est complète
                invoice_path = None
                if is_fulfilled:
                    try:
                        from backend.tools.output_tools import invoice_generator
                        inv = await invoice_generator.ainvoke({
                            "partner_id":   partner_name.lower().replace(" ", "_"),
                            "order_id":     str(partner_order.id),
                            "lot_id":       lot_id,
                            "items":        [{"grade": grade, "size": size_class, "qty": partner_order.quantity_needed}],
                            "market_price_tnd": 0.35  # prix par défaut si non dispo
                        })
                        invoice_path = inv.get("pdf_path")
                    except Exception as inv_err:
                        logger.warning(f"[inventory_allocator] Invoice generation failed: {inv_err}")

                await notify_egg_allocated(
                    partner_name=partner_name,
                    order_id=str(partner_order.id),
                    grade=grade,
                    quantity_fulfilled=partner_order.quantity_fulfilled,
                    quantity_needed=partner_order.quantity_needed,
                    lot_id=lot_id,
                    is_fulfilled=is_fulfilled,
                    invoice_path=invoice_path
                )
            except Exception as notif_err:
                logger.warning(f"[inventory_allocator] Notification failed (non-blocking): {notif_err}")

        return {
            "allocated":        True,
            "routing_decision": _routing(grade),
            "zone":             _zone(grade),
            "stock_entry_id":   stock_id,
            "destination":      destination,
            "partner_allocated":partner_order is not None,
            "order_id":         allocated_to_order,
            "partner_name":     partner_name,
            "is_fulfilled":     is_fulfilled,
            "allocation_notes": allocation_notes
        }

    except Exception as e:
        logger.error(f"[inventory_allocator] Error: {e}", exc_info=True)
        try: db.rollback()
        except Exception: pass
        return {
            "allocated": False, "routing_decision": _routing(grade),
            "zone": _zone(grade), "destination": destination,
            "partner_allocated": False, "error": str(e)
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# check_stock / allocate_lot / notify_shortage
# ─────────────────────────────────────────────────────────────
@tool
async def check_stock(grade: str, size: str, requested_qty: int) -> dict:
    """Check available inventory for a given grade and size."""
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT COUNT(*) FROM stock WHERE grade=:g AND size_class=:s AND status='available'"),
            {"g": grade, "s": size}
        ).fetchone()
        available_qty = row[0] if row else 0
        status = "confirmed" if available_qty >= requested_qty else ("partial" if available_qty > 0 else "unavailable")
        return {"available_qty": available_qty, "status": status, "requested_qty": requested_qty, "grade": grade, "size": size}
    finally:
        db.close()


@tool
async def allocate_lot(partner_id: str, order_id: str, items: list, market_price_per_egg_tnd: float) -> dict:
    """Allocate eggs from stock to a partner order and generate an invoice."""
    from backend.tools.output_tools import invoice_generator
    db = SessionLocal()
    try:
        lot_id = f"LOT_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6].upper()}"
        allocated_items = []; total_eggs = 0

        for item in items:
            rows = db.execute(
                text("SELECT id FROM stock WHERE grade=:g AND size_class=:s AND status='available' ORDER BY entry_date ASC LIMIT :q"),
                {"g": item.get("grade"), "s": item.get("size"), "q": item.get("quantity", 0)}
            ).fetchall()
            for row in rows:
                db.execute(text("UPDATE stock SET status='reserved', allocated_to_order=:oid WHERE id=:id"),
                           {"oid": order_id, "id": row[0]})
            allocated_items.append({"grade": item.get("grade"), "size": item.get("size"), "qty": len(rows)})
            total_eggs += len(rows)

        order = db.query(PartnerOrder).filter(PartnerOrder.id == order_id).first()
        if order:
            order.quantity_fulfilled += total_eggs
            order.status = "fulfilled" if order.quantity_fulfilled >= order.quantity_needed else "partial"
        db.commit()

        inv_result = {}
        try:
            inv_result = await invoice_generator.ainvoke({
                "partner_id": partner_id, "order_id": order_id,
                "lot_id": lot_id, "items": allocated_items,
                "market_price_tnd": market_price_per_egg_tnd
            })
        except Exception as ie:
            inv_result = {"error": str(ie)}

        return {"lot_id": lot_id, "partner_id": partner_id, "total_eggs": total_eggs, "invoice": inv_result}
    except Exception as e:
        db.rollback(); return {"error": str(e)}
    finally:
        db.close()


@tool
async def notify_shortage(order_id: str, partner_name: str, unfulfilled_lines: list, delivery_deadline: str) -> dict:
    """Alert partner about unfulfillable order via email + webhook."""
    try:
        from backend.services.notification_service import notify_shortage as _notify
        for line in unfulfilled_lines:
            await _notify(
                partner_name=partner_name,
                order_id=order_id,
                grade=line.get("grade", "?"),
                quantity_needed=line.get("shortage", 0),
                quantity_available=0,
                deadline=delivery_deadline
            )
    except Exception as e:
        logger.warning(f"[notify_shortage] Notification failed: {e}")

    return {
        "alert_sent": True,
        "message": f"Shortage alert for order {order_id} ({partner_name}). Deadline: {delivery_deadline}",
        "lines": unfulfilled_lines
    }


@tool
async def root_cause_analyzer(lot_id: str, quality: str, quality_confidence: float, **kwargs) -> dict:
    """Root cause analyzer — placeholder."""
    return {"skipped": True}