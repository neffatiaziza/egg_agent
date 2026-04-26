# backend/services/notification_service.py
"""
Service de notification SIMULÉ — projet universitaire.

Aucun vrai email n'est envoyé.
Aucun vrai ERP n'existe.
Tout est simulé localement et stocké dans la DB.

Pour démontrer le système :
  - Les notifications apparaissent dans /api/notifications
  - Un faux ERP est simulé dans /api/fake-erp
  - Tout est visible dans le dashboard
"""

import json
import logging
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── Partenaires fictifs du projet ────────────────────────────
FAKE_PARTNERS = {
    "Carrefour": {
        "email":    "achat@carrefour-fictif.tn",
        "phone":    "+216 71 000 001",
        "erp_type": "SAP (simulé)",
        "contact":  "Mohamed Ben Ali"
    },
    "Monoprix": {
        "email":    "commandes@monoprix-fictif.tn",
        "phone":    "+216 71 000 002",
        "erp_type": "Oracle (simulé)",
        "contact":  "Fatma Trabelsi"
    },
    "MG": {
        "email":    "approvisionnement@mg-fictif.tn",
        "phone":    "+216 71 000 003",
        "erp_type": "Odoo (simulé)",
        "contact":  "Ahmed Chaabane"
    }
}


# ─────────────────────────────────────────────────────────────
# Stocker les notifications simulées en DB
# ─────────────────────────────────────────────────────────────

def _ensure_notifications_table():
    """Crée la table notifications si elle n'existe pas."""
    db = SessionLocal()
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS notifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                partner     TEXT,
                event_type  TEXT,
                order_id    TEXT,
                lot_id      TEXT,
                grade       TEXT,
                message     TEXT,
                payload     TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                delivered   INTEGER DEFAULT 0
            )
        """))
        db.commit()
    finally:
        db.close()

_ensure_notifications_table()


def _store_notification(
    partner: str,
    event_type: str,
    order_id: str,
    lot_id: str,
    grade: str,
    message: str,
    payload: dict
):
    """Sauvegarde la notification simulée en DB."""
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO notifications (partner, event_type, order_id, lot_id, grade, message, payload, created_at, delivered)
            VALUES (:partner, :event, :order_id, :lot_id, :grade, :msg, :payload, :ts, 1)
        """), {
            "partner":  partner,
            "event":    event_type,
            "order_id": order_id,
            "lot_id":   lot_id,
            "grade":    grade,
            "msg":      message,
            "payload":  json.dumps(payload),
            "ts":       datetime.utcnow()
        })
        db.commit()
    except Exception as e:
        logger.error(f"[notification] Store failed: {e}")
    finally:
        db.close()


def _log_notification(event: str, partner: str, message: str, payload: dict):
    """Affiche la notification dans le terminal de manière lisible."""
    print("\n" + "="*60)
    print(f"📧 NOTIFICATION SIMULÉE [{event.upper()}]")
    print(f"   Partenaire : {partner}")
    partner_info = FAKE_PARTNERS.get(partner, {})
    if partner_info:
        print(f"   Contact    : {partner_info.get('contact')}")
        print(f"   Email      : {partner_info.get('email')} (fictif)")
        print(f"   ERP        : {partner_info.get('erp_type')}")
    print(f"   Message    : {message}")
    print(f"   Payload    : {json.dumps(payload, indent=2, ensure_ascii=False)}")
    print("="*60 + "\n")


# ─────────────────────────────────────────────────────────────
# API publique
# ─────────────────────────────────────────────────────────────

async def notify_egg_allocated(
    partner_name: str,
    order_id: str,
    grade: str,
    quantity_fulfilled: int,
    quantity_needed: int,
    lot_id: str,
    is_fulfilled: bool = False,
    invoice_path = None
):
    """
    Notification partenaire :
    - Progression → log terminal seulement (pas de mail)
    - Commande complète → mail envoyé au technicien (qui joue le rôle partenaire en test)
    """
    pct = int(quantity_fulfilled / quantity_needed * 100) if quantity_needed > 0 else 0
 
    if is_fulfilled:
        event   = "order_fulfilled"
        message = (
            f"✅ Commande #{order_id} COMPLÈTE — "
            f"{quantity_needed} œufs Grade {grade} prêts pour livraison à {partner_name}"
        )
    else:
        event   = "egg_allocated"
        message = (
            f"🥚 Œuf Grade {grade} affecté à {partner_name} — "
            f"{quantity_fulfilled}/{quantity_needed} ({pct}%)"
        )
 
    payload = {
        "event":              event,
        "order_id":           order_id,
        "partner":            partner_name,
        "grade":              grade,
        "quantity_fulfilled": quantity_fulfilled,
        "quantity_needed":    quantity_needed,
        "progress_pct":       pct,
        "lot_id":             lot_id,
        "timestamp":          datetime.utcnow().isoformat(),
        "note":               "SIMULATION — projet universitaire El Mazraa"
    }
 
    # Log terminal toujours
    _log_notification(event, partner_name, message, payload)
 
    # Stocker en DB toujours
    _store_notification(partner_name, event, order_id, lot_id, grade, message, payload)
 
    # ── Mail UNIQUEMENT quand la commande est complète ─────────
    if is_fulfilled:
        try:
            import os, smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
 
            SMTP_HOST     = os.getenv("SMTP_HOST",        "smtp.gmail.com")
            SMTP_PORT     = int(os.getenv("SMTP_PORT",    "587"))
            SMTP_USER     = os.getenv("SMTP_USER",        "")
            SMTP_PASSWORD = os.getenv("SMTP_PASSWORD",    "")
            TECH_EMAIL    = os.getenv("TECHNICIAN_EMAIL", "")
            TECH_NAME     = os.getenv("TECHNICIAN_NAME",  "Technicien")
 
            if not (SMTP_USER and SMTP_PASSWORD and TECH_EMAIL):
                logger.info(f"[notification] SMTP non configuré — commande {order_id} complète loggée seulement")
                return
 
            html = f"""
            <div style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto">
              <div style="background:#1a3d2b;padding:16px 24px;color:#fff">
                <strong>🥚 QC Harvest Agent</strong> / El Mazraa · Groupe Poulina
              </div>
              <div style="background:#fff;border:1px solid #e0ddd6;padding:24px">
                <h2 style="color:#1a3d2b;margin-bottom:4px">✅ Commande complète</h2>
                <p style="color:#6b6b60;font-size:13px">La commande de {partner_name} est entièrement constituée.</p>
 
                <div style="background:#d8ede3;border-radius:8px;padding:16px;margin:16px 0">
                  <table style="width:100%;border-collapse:collapse;font-size:14px">
                    <tr><td style="color:#3b6d11;padding:5px 0">Partenaire</td><td style="font-weight:600">{partner_name}</td></tr>
                    <tr><td style="color:#3b6d11;padding:5px 0">Commande</td><td style="font-weight:600">#{order_id}</td></tr>
                    <tr><td style="color:#3b6d11;padding:5px 0">Grade</td><td style="font-weight:600">{grade}</td></tr>
                    <tr><td style="color:#3b6d11;padding:5px 0">Quantité</td><td style="font-weight:600">{quantity_needed} œufs</td></tr>
                    <tr><td style="color:#3b6d11;padding:5px 0">Dernier lot</td><td style="font-family:monospace">{lot_id}</td></tr>
                    <tr><td style="color:#3b6d11;padding:5px 0">Date</td><td>{datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</td></tr>
                  </table>
                </div>
 
                <p style="font-size:13px;color:#1a3d2b;font-weight:600">
                  Action requise : Préparer l'expédition et générer la facture finale.
                </p>
              </div>
              <div style="padding:12px 24px;font-size:11px;color:#9b9b8e;text-align:center">
                Egg-Agent · El Mazraa · Groupe Poulina — Système automatique<br>
                <em>Test universitaire — mail envoyé à {TECH_EMAIL}</em>
              </div>
            </div>"""
 
            msg             = MIMEMultipart("alternative")
            msg["From"]     = SMTP_USER
            msg["To"]       = TECH_EMAIL
            msg["Subject"]  = f"✅ Commande {partner_name} #{order_id} complète — {quantity_needed} œufs Grade {grade}"
            msg.attach(MIMEText(html, "html"))
 
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, TECH_EMAIL, msg.as_string())
 
            logger.info(f"[notification] ✅ Mail commande complète envoyé à {TECH_EMAIL} pour {partner_name} order #{order_id}")
 
        except Exception as e:
            logger.error(f"[notification] ❌ Mail commande échoué: {e}")
 
    logger.info(f"[notification] {event} → {partner_name} | {quantity_fulfilled}/{quantity_needed} Grade {grade}")

async def notify_shortage(
    partner_name: str,
    order_id: str,
    grade: str,
    quantity_needed: int,
    quantity_available: int,
    deadline: Optional[str] = None
):
    """Simule une alerte de rupture de stock."""
    shortage = quantity_needed - quantity_available
    message  = (
        f"⚠️ RUPTURE — Commande #{order_id} : "
        f"manque {shortage} œufs Grade {grade}"
    )

    payload = {
        "event":               "stock_shortage",
        "order_id":            order_id,
        "partner":             partner_name,
        "grade":               grade,
        "quantity_needed":     quantity_needed,
        "quantity_available":  quantity_available,
        "shortage":            shortage,
        "deadline":            deadline,
        "timestamp":           datetime.utcnow().isoformat(),
        "note":                "SIMULATION — projet universitaire El Mazraa"
    }

    _log_notification("stock_shortage", partner_name, message, payload)
    _store_notification(
        partner=partner_name, event_type="stock_shortage",
        order_id=order_id, lot_id="N/A",
        grade=grade, message=message, payload=payload
    )

def log_discovery_event(grade: str, size: str, quantity: int, leads: list, offer_price: float):
    """Logs a partner discovery event to the database when surplus is detected."""
    payload = {
        "size": size,
        "quantity": quantity,
        "leads_found": len(leads),
        "leads": leads,
        "price_tnd": offer_price
    }
    
    message = f"{len(leads)} leads trouvés pour Grade {grade}"
    
    # Log terminal
    _log_notification("discovery", "Discovery Tool", message, payload)
    
    # Store in DB
    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text("""
            INSERT INTO notifications (partner, event_type, order_id, lot_id, grade, message, payload, created_at, delivered)
            VALUES (:partner, :event, :order_id, :lot_id, :grade, :msg, :payload, :ts, 1)
        """), {
            "partner": "Discovery Tool",
            "event": "discovery",
            "order_id": "SURPLUS",
            "lot_id": "N/A",
            "grade": grade,
            "msg": message,
            "payload": json.dumps(payload),
            "ts": datetime.utcnow()
        })
        db.commit()
    except Exception as e:
        logger.error(f"[notification] log_discovery_event failed: {e}")
    finally:
        db.close()