# backend/tools/db_query_tool.py
"""
Tool permettant à l'agent de répondre à des questions en langage naturel
sur la base de données SQLite (stocks, commandes partenaires, statistiques...).

Exemples de questions :
  - "Combien d'œufs grade A aujourd'hui ?"
  - "Quelle commande Carrefour est en cours ?"
  - "Quel est le taux de rejet cette semaine ?"
  - "Montre les alertes non résolues"
  - "Quel est le stock disponible par grade ?"
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from langchain_core.tools import tool
from sqlalchemy import func, text
from backend.db.database import SessionLocal
from backend.db.models import (
    Lot, Alert, Stock, PartnerOrder,
    DispatchLog, QualityIncident, Feedback
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _today_range():
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end   = start + timedelta(days=1)
    return start, end


def _week_range():
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day) - timedelta(days=today.weekday())
    end   = start + timedelta(days=7)
    return start, end


def _month_range():
    today = datetime.utcnow()
    start = datetime(today.year, today.month, 1)
    # Premier jour du mois suivant
    if today.month == 12:
        end = datetime(today.year + 1, 1, 1)
    else:
        end = datetime(today.year, today.month + 1, 1)
    return start, end


# ─────────────────────────────────────────────────────────────
# TOOL PRINCIPAL
# ─────────────────────────────────────────────────────────────

@tool
async def db_query_tool(
    query_type: str,
    grade: Optional[str] = None,
    partner_name: Optional[str] = None,
    period: Optional[str] = "today",
    limit: Optional[int] = 10
) -> dict:
    """
    Query the egg quality database to answer business questions.

    query_type options:
      - "stock_by_grade"        : Stock disponible par grade (et par partenaire si précisé)
      - "partner_orders"        : Commandes partenaires (toutes ou filtrées par partner_name)
      - "order_fulfillment"     : Taux de remplissage des commandes partenaires
      - "lots_today"            : Lots analysés aujourd'hui
      - "rejection_rate"        : Taux de rejet (period: today/week/month)
      - "grade_distribution"    : Distribution des grades (period: today/week/month)
      - "alerts_active"         : Alertes actives non résolues
      - "top_defects"           : Défauts les plus fréquents
      - "dispatch_log"          : Historique des expéditions (par partenaire si précisé)
      - "kpi_summary"           : Résumé KPI complet (today/week/month)
      - "egg_count_by_grade"    : Nombre d'œufs par grade (filtrable par grade et période)
      - "partner_shortage"      : Commandes partenaires en risque de rupture
      - "recent_lots"           : Derniers lots analysés

    period: "today" | "week" | "month" | "all"
    grade: filter by grade (AA/A/B/C/D/E)
    partner_name: filter by partner name (e.g. "Carrefour")
    limit: max results to return
    """
    db = SessionLocal()
    try:
        # ── Sélection de la plage temporelle ──────────────
        if period == "today":
            t_start, t_end = _today_range()
        elif period == "week":
            t_start, t_end = _week_range()
        elif period == "month":
            t_start, t_end = _month_range()
        else:  # "all"
            t_start = datetime(2000, 1, 1)
            t_end   = datetime(2100, 1, 1)

        # ══════════════════════════════════════════════════
        # 1. STOCK PAR GRADE
        # ══════════════════════════════════════════════════
        if query_type == "stock_by_grade":
            q = db.query(
                Stock.grade,
                Stock.size_class,
                func.count(Stock.id).label("quantity"),
                Stock.storage_zone
            ).filter(Stock.status == "available")

            if grade:
                q = q.filter(Stock.grade == grade)

            results = q.group_by(Stock.grade, Stock.size_class, Stock.storage_zone).all()

            data = [
                {
                    "grade": r.grade,
                    "size_class": r.size_class,
                    "quantity": r.quantity,
                    "storage_zone": r.storage_zone
                }
                for r in results
            ]
            total = sum(r["quantity"] for r in data)

            return {
                "query": "stock_by_grade",
                "period": period,
                "grade_filter": grade,
                "total_available": total,
                "breakdown": data,
                "summary": f"{total} œufs disponibles en stock" + (f" (grade {grade})" if grade else "")
            }

        # ══════════════════════════════════════════════════
        # 2. COMMANDES PARTENAIRES
        # ══════════════════════════════════════════════════
        elif query_type == "partner_orders":
            q = db.query(PartnerOrder)
            if partner_name:
                q = q.filter(PartnerOrder.partner_name.ilike(f"%{partner_name}%"))
            orders = q.order_by(PartnerOrder.priority.asc(), PartnerOrder.deadline_date.asc()).all()

            data = []
            for o in orders:
                pct = (o.quantity_fulfilled / o.quantity_needed * 100) if o.quantity_needed > 0 else 0
                data.append({
                    "id": o.id,
                    "partner": o.partner_name,
                    "grade": o.required_grade,
                    "size": o.required_size,
                    "needed": o.quantity_needed,
                    "fulfilled": o.quantity_fulfilled,
                    "fulfillment_pct": round(pct, 1),
                    "status": o.status,
                    "deadline": o.deadline_date.isoformat() if o.deadline_date else None,
                    "priority": o.priority
                })

            active   = [d for d in data if d["status"] in ("pending", "partial")]
            complete = [d for d in data if d["status"] == "fulfilled"]

            return {
                "query": "partner_orders",
                "partner_filter": partner_name,
                "total_orders": len(data),
                "active_orders": len(active),
                "fulfilled_orders": len(complete),
                "orders": data,
                "summary": (
                    f"{len(active)} commande(s) active(s)" +
                    (f" pour {partner_name}" if partner_name else "") +
                    f", {len(complete)} complétée(s)"
                )
            }

        # ══════════════════════════════════════════════════
        # 3. TAUX DE REMPLISSAGE DES COMMANDES
        # ══════════════════════════════════════════════════
        elif query_type == "order_fulfillment":
            q = db.query(PartnerOrder).filter(
                PartnerOrder.status.in_(["pending", "partial"])
            )
            if partner_name:
                q = q.filter(PartnerOrder.partner_name.ilike(f"%{partner_name}%"))

            orders = q.all()
            data = []
            for o in orders:
                pct       = (o.quantity_fulfilled / o.quantity_needed * 100) if o.quantity_needed > 0 else 0
                remaining = o.quantity_needed - o.quantity_fulfilled
                days_left = (o.deadline_date - datetime.utcnow()).days if o.deadline_date else None
                at_risk   = (days_left is not None and days_left <= 2 and remaining > 0)

                data.append({
                    "partner": o.partner_name,
                    "grade": o.required_grade,
                    "size": o.required_size,
                    "needed": o.quantity_needed,
                    "fulfilled": o.quantity_fulfilled,
                    "remaining": remaining,
                    "fulfillment_pct": round(pct, 1),
                    "days_until_deadline": days_left,
                    "at_risk": at_risk,
                    "status": o.status
                })

            at_risk_count = sum(1 for d in data if d["at_risk"])
            return {
                "query": "order_fulfillment",
                "active_orders": len(data),
                "at_risk_orders": at_risk_count,
                "orders": data,
                "summary": (
                    f"{len(data)} commande(s) active(s), "
                    f"{at_risk_count} en risque de rupture (<2 jours)"
                )
            }

        # ══════════════════════════════════════════════════
        # 4. LOTS ANALYSÉS AUJOURD'HUI
        # ══════════════════════════════════════════════════
        elif query_type == "lots_today":
            lots = db.query(Lot).filter(
                Lot.timestamp >= t_start,
                Lot.timestamp < t_end
            ).order_by(Lot.timestamp.desc()).limit(limit).all()

            data = [
                {
                    "lot_id": l.lot_id,
                    "grade": l.grade,
                    "destination": l.destination,
                    "confidence": l.confidence,
                    "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                    "needs_review": bool(l.needs_human_review)
                }
                for l in lots
            ]
            return {
                "query": "lots_today",
                "period": period,
                "count": len(data),
                "lots": data,
                "summary": f"{len(data)} lot(s) analysé(s) sur la période '{period}'"
            }

        # ══════════════════════════════════════════════════
        # 5. TAUX DE REJET
        # ══════════════════════════════════════════════════
        elif query_type == "rejection_rate":
            REJECTED = {"E", "D", "Rejected", "Oeuf industriel", "UNGRADED"}

            all_lots = db.query(Lot).filter(
                Lot.timestamp >= t_start,
                Lot.timestamp < t_end
            ).all()

            total    = len(all_lots)
            rejected = sum(1 for l in all_lots if l.grade in REJECTED)
            rate     = (rejected / total * 100) if total > 0 else 0.0

            return {
                "query": "rejection_rate",
                "period": period,
                "total_analyzed": total,
                "total_rejected": rejected,
                "rejection_rate_pct": round(rate, 2),
                "summary": (
                    f"Taux de rejet ({period}) : {rate:.1f}% "
                    f"({rejected}/{total} œufs rejetés)"
                )
            }

        # ══════════════════════════════════════════════════
        # 6. DISTRIBUTION DES GRADES
        # ══════════════════════════════════════════════════
        elif query_type == "grade_distribution":
            rows = db.query(
                Lot.grade,
                func.count(Lot.id).label("count")
            ).filter(
                Lot.timestamp >= t_start,
                Lot.timestamp < t_end
            ).group_by(Lot.grade).all()

            total = sum(r.count for r in rows)
            data  = [
                {
                    "grade": r.grade,
                    "count": r.count,
                    "percentage": round(r.count / total * 100, 1) if total > 0 else 0
                }
                for r in sorted(rows, key=lambda x: x.count, reverse=True)
            ]

            return {
                "query": "grade_distribution",
                "period": period,
                "total": total,
                "distribution": data,
                "summary": f"Distribution des grades ({period}) : " +
                           ", ".join(f"{d['grade']}={d['count']} ({d['percentage']}%)" for d in data)
            }

        # ══════════════════════════════════════════════════
        # 7. ALERTES ACTIVES
        # ══════════════════════════════════════════════════
        elif query_type == "alerts_active":
            alerts = db.query(Alert).order_by(
                Alert.timestamp.desc()
            ).limit(limit).all()

            data = [
                {
                    "id": a.id,
                    "lot_id": a.lot_id,
                    "message": a.message,
                    "rejection_rate": a.rejection_rate,
                    "timestamp": a.timestamp.isoformat() if a.timestamp else None
                }
                for a in alerts
            ]

            critical = [d for d in data if "[CRITICAL]" in d["message"]]
            warnings = [d for d in data if "[WARNING]"  in d["message"]]
            errors   = [d for d in data if "[ERROR]"    in d["message"]]

            return {
                "query": "alerts_active",
                "total_alerts": len(data),
                "critical": len(critical),
                "warnings": len(warnings),
                "errors": len(errors),
                "alerts": data,
                "summary": (
                    f"{len(data)} alerte(s) — "
                    f"{len(critical)} critique(s), {len(warnings)} avertissement(s), {len(errors)} erreur(s)"
                )
            }

        # ══════════════════════════════════════════════════
        # 8. DÉFAUTS LES PLUS FRÉQUENTS
        # ══════════════════════════════════════════════════
        elif query_type == "top_defects":
            incidents = db.query(
                QualityIncident.defect_type,
                func.count(QualityIncident.id).label("count")
            ).group_by(QualityIncident.defect_type).order_by(
                func.count(QualityIncident.id).desc()
            ).limit(limit).all()

            data = [{"defect": r.defect_type, "count": r.count} for r in incidents]

            # Fallback : analyser les defects_detected dans lots
            if not data:
                lots = db.query(Lot.defects_detected).filter(
                    Lot.defects_detected.isnot(None),
                    Lot.timestamp >= t_start
                ).all()

                defect_counts = {}
                for lot in lots:
                    try:
                        defects = json.loads(lot.defects_detected) if lot.defects_detected else []
                        if isinstance(defects, list):
                            for d in defects:
                                defect_counts[d] = defect_counts.get(d, 0) + 1
                    except Exception:
                        pass

                data = [
                    {"defect": k, "count": v}
                    for k, v in sorted(defect_counts.items(), key=lambda x: x[1], reverse=True)
                ][:limit]

            return {
                "query": "top_defects",
                "period": period,
                "defects": data,
                "summary": "Défauts les plus fréquents : " +
                           (", ".join(f"{d['defect']} ({d['count']}x)" for d in data[:5]) or "aucun enregistré")
            }

        # ══════════════════════════════════════════════════
        # 9. HISTORIQUE DES EXPÉDITIONS
        # ══════════════════════════════════════════════════
        elif query_type == "dispatch_log":
            q = db.query(DispatchLog).order_by(DispatchLog.dispatched_at.desc())
            if partner_name:
                q = q.filter(DispatchLog.partner_name.ilike(f"%{partner_name}%"))
            q = q.filter(
                DispatchLog.dispatched_at >= t_start,
                DispatchLog.dispatched_at < t_end
            ).limit(limit)

            logs = q.all()
            data = [
                {
                    "lot_id": l.lot_id,
                    "partner": l.partner_name,
                    "grade": l.grade,
                    "quantity": l.quantity,
                    "dispatched_at": l.dispatched_at.isoformat() if l.dispatched_at else None,
                    "order_id": l.order_id
                }
                for l in logs
            ]

            total_qty = sum(d["quantity"] for d in data)
            return {
                "query": "dispatch_log",
                "period": period,
                "partner_filter": partner_name,
                "total_dispatched": total_qty,
                "entries": len(data),
                "log": data,
                "summary": f"{total_qty} œuf(s) expédié(s)" +
                           (f" à {partner_name}" if partner_name else "") +
                           f" sur la période '{period}'"
            }

        # ══════════════════════════════════════════════════
        # 10. KPI RÉSUMÉ COMPLET
        # ══════════════════════════════════════════════════
        elif query_type == "kpi_summary":
            REJECTED = {"E", "D", "Rejected", "Oeuf industriel", "UNGRADED"}

            lots = db.query(Lot).filter(
                Lot.timestamp >= t_start,
                Lot.timestamp < t_end
            ).all()

            total    = len(lots)
            rejected = sum(1 for l in lots if l.grade in REJECTED)
            rate     = (rejected / total * 100) if total > 0 else 0.0

            grade_dist = {}
            for l in lots:
                grade_dist[l.grade] = grade_dist.get(l.grade, 0) + 1

            avg_conf = sum(l.confidence or 0 for l in lots) / total if total > 0 else 0.0

            # Stock
            stock_rows = db.query(
                Stock.grade,
                func.count(Stock.id).label("qty")
            ).filter(Stock.status == "available").group_by(Stock.grade).all()
            stock_summary = {r.grade: r.qty for r in stock_rows}

            # Commandes actives
            active_orders = db.query(PartnerOrder).filter(
                PartnerOrder.status.in_(["pending", "partial"])
            ).count()

            # Alertes récentes
            recent_alerts = db.query(Alert).order_by(
                Alert.timestamp.desc()
            ).limit(3).all()

            return {
                "query": "kpi_summary",
                "period": period,
                "production": {
                    "total_analyzed": total,
                    "total_rejected": rejected,
                    "rejection_rate_pct": round(rate, 2),
                    "avg_confidence_pct": round(avg_conf * 100, 1),
                    "grade_distribution": grade_dist
                },
                "inventory": {
                    "available_by_grade": stock_summary,
                    "total_available": sum(stock_summary.values())
                },
                "orders": {
                    "active_orders": active_orders
                },
                "alerts": [
                    {
                        "message": a.message,
                        "timestamp": a.timestamp.isoformat() if a.timestamp else None
                    }
                    for a in recent_alerts
                ],
                "summary": (
                    f"[{period.upper()}] {total} lots analysés — "
                    f"Rejet: {rate:.1f}% — "
                    f"Stock: {sum(stock_summary.values())} œufs — "
                    f"{active_orders} commande(s) active(s)"
                )
            }

        # ══════════════════════════════════════════════════
        # 11. NOMBRE D'ŒUFS PAR GRADE
        # ══════════════════════════════════════════════════
        elif query_type == "egg_count_by_grade":
            q = db.query(
                Lot.grade,
                func.count(Lot.id).label("count")
            ).filter(
                Lot.timestamp >= t_start,
                Lot.timestamp < t_end
            )
            if grade:
                q = q.filter(Lot.grade == grade)

            rows  = q.group_by(Lot.grade).all()
            total = sum(r.count for r in rows)
            data  = [{"grade": r.grade, "count": r.count} for r in rows]

            return {
                "query": "egg_count_by_grade",
                "period": period,
                "grade_filter": grade,
                "total": total,
                "breakdown": data,
                "summary": (
                    f"{total} œuf(s) grade {grade} ({period})" if grade
                    else f"{total} œuf(s) au total ({period}) — " +
                         ", ".join(f"{d['grade']}: {d['count']}" for d in data)
                )
            }

        # ══════════════════════════════════════════════════
        # 12. COMMANDES EN RISQUE DE RUPTURE
        # ══════════════════════════════════════════════════
        elif query_type == "partner_shortage":
            orders = db.query(PartnerOrder).filter(
                PartnerOrder.status.in_(["pending", "partial"])
            ).all()

            at_risk = []
            for o in orders:
                remaining = o.quantity_needed - o.quantity_fulfilled
                days_left = (o.deadline_date - datetime.utcnow()).days if o.deadline_date else None

                # Chercher le stock disponible pour ce grade/taille
                available = db.query(Stock).filter(
                    Stock.grade == o.required_grade,
                    Stock.size_class == o.required_size,
                    Stock.status == "available"
                ).count()

                if remaining > 0 and (days_left is not None and days_left <= 3 or available < remaining):
                    at_risk.append({
                        "partner": o.partner_name,
                        "grade": o.required_grade,
                        "size": o.required_size,
                        "needed": o.quantity_needed,
                        "fulfilled": o.quantity_fulfilled,
                        "remaining": remaining,
                        "available_in_stock": available,
                        "stock_gap": max(0, remaining - available),
                        "days_until_deadline": days_left,
                        "urgency": "critique" if days_left is not None and days_left <= 1 else "élevée"
                    })

            return {
                "query": "partner_shortage",
                "at_risk_count": len(at_risk),
                "at_risk_orders": at_risk,
                "summary": (
                    f"{len(at_risk)} commande(s) partenaire en risque de rupture" if at_risk
                    else "Aucune commande partenaire en risque de rupture"
                )
            }

        # ══════════════════════════════════════════════════
        # 13. DERNIERS LOTS ANALYSÉS
        # ══════════════════════════════════════════════════
        elif query_type == "recent_lots":
            lots = db.query(Lot).order_by(
                Lot.timestamp.desc()
            ).limit(limit).all()

            data = [
                {
                    "lot_id": l.lot_id,
                    "grade": l.grade,
                    "destination": l.destination,
                    "confidence": round(l.confidence * 100, 1) if l.confidence else 0,
                    "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                    "needs_review": bool(l.needs_human_review),
                    "defects": l.defects_detected
                }
                for l in lots
            ]

            return {
                "query": "recent_lots",
                "count": len(data),
                "lots": data,
                "summary": f"Les {len(data)} derniers lots analysés"
            }

        else:
            return {
                "error": f"Unknown query_type: '{query_type}'",
                "available_types": [
                    "stock_by_grade", "partner_orders", "order_fulfillment",
                    "lots_today", "rejection_rate", "grade_distribution",
                    "alerts_active", "top_defects", "dispatch_log",
                    "kpi_summary", "egg_count_by_grade", "partner_shortage",
                    "recent_lots"
                ]
            }

    except Exception as e:
        logger.error(f"[db_query_tool] Error: {e}", exc_info=True)
        return {"error": str(e), "query_type": query_type}
    finally:
        db.close()
