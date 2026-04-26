# backend/routes/notifications.py
"""
Routes pour le dashboard de notifications et le faux ERP simulé.
Pour le projet universitaire — tout est fictif.
"""

from fastapi import APIRouter
from sqlalchemy import text
from backend.db.database import SessionLocal
from backend.db.models import PartnerOrder
import json
from datetime import datetime

router = APIRouter(prefix="/api", tags=["notifications"])


# ─────────────────────────────────────────────────────────────
# Notifications simulées
# ─────────────────────────────────────────────────────────────

@router.get("/notifications")
def get_notifications(partner: str = None, limit: int = 50):
    """
    Récupère les dernières notifications depuis la base de données.
    Utilisé par le dashboard React.
    """
    db = SessionLocal()
    try:
        query = "SELECT * FROM notifications"
        params = {"limit": limit}
        
        if partner:
            query += " WHERE partner = :partner"
            params["partner"] = partner
            
        query += " ORDER BY created_at DESC LIMIT :limit"
        
        rows = db.execute(text(query), params).mappings().all()
        
        notifications = []
        for row in rows:
            notif = dict(row)
            # Désérialiser le payload JSON si présent
            if notif.get("payload"):
                try:
                    notif["payload"] = json.loads(notif["payload"])
                except:
                    pass
            notifications.append(notif)
            
        return {"notifications": notifications, "count": len(notifications)}
    finally:
        db.close()
