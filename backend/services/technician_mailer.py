# backend/services/technician_mailer.py
"""
Envoie un vrai email au technicien El Mazraa quand un lot est validé.

Configuration dans .env :
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=votre@gmail.com
  SMTP_PASSWORD=votre_app_password_gmail
  TECHNICIAN_EMAIL=technicien@elmazraa.tn   ← email du technicien
  TECHNICIAN_NAME=Mohamed                    ← nom affiché
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

SMTP_HOST        = os.getenv("SMTP_HOST",        "smtp.gmail.com")
SMTP_PORT        = int(os.getenv("SMTP_PORT",    "587"))
SMTP_USER        = os.getenv("SMTP_USER",        "")
SMTP_PASSWORD    = os.getenv("SMTP_PASSWORD",    "")
TECH_EMAIL       = os.getenv("TECHNICIAN_EMAIL", "")
TECH_NAME        = os.getenv("TECHNICIAN_NAME",  "Technicien")
SMTP_ENABLED     = bool(SMTP_USER and SMTP_PASSWORD and TECH_EMAIL)


def send_lot_validated(
    lot_id: str,
    grade: str,
    destination: str,
    confidence: float,
    grading_source: str,
    defects: list,
    partner_name: Optional[str],
    allocation_notes: Optional[str],
    pdf_path: Optional[str] = None,
    needs_review: bool = False
):
    """
    Envoie un email au technicien El Mazraa après validation d'un lot.
    Si SMTP non configuré → log dans le terminal uniquement.
    """
    subject = f"[{'⚠️ REVUE REQUISE' if needs_review else '✅ Lot validé'}] {lot_id} — Grade {grade}"

    grade_color = {
        "AA": "#1a3d2b", "A": "#1a3d2b",
        "B":  "#1e40af",
        "C":  "#b7791f", "D": "#b7791f",
        "E":  "#c0392b", "UNGRADED": "#6b6b60"
    }.get(grade, "#6b6b60")

    defects_html = "".join(
        f'<span style="background:#fdecea;color:#c0392b;padding:2px 8px;border-radius:4px;font-size:12px;margin:2px;display:inline-block">⚠ {d}</span>'
        for d in (defects or [])
    ) or '<span style="color:#6b6b60">Aucun défaut détecté</span>'

    partner_html = f"""
      <tr>
        <td style="padding:8px 0;color:#6b6b60;font-size:13px;border-bottom:1px solid #e0ddd6">Partenaire affecté</td>
        <td style="padding:8px 0;font-weight:600;font-size:13px;border-bottom:1px solid #e0ddd6;color:#1a3d2b">
          🏪 {partner_name} — {allocation_notes or ''}
        </td>
      </tr>""" if partner_name else ""

    review_banner = """
      <div style="background:#fdecea;border:1px solid #f5c6c2;border-radius:6px;padding:14px 18px;margin-bottom:16px">
        <strong style="color:#c0392b">⚠️ Revue humaine requise</strong><br>
        <span style="font-size:13px;color:#6b6b60">Ce lot nécessite votre validation manuelle avant expédition.</span>
      </div>""" if needs_review else ""

    html = f"""
    <div style="font-family:'IBM Plex Sans',Arial,sans-serif;max-width:580px;margin:0 auto;background:#f4f2ed;padding:0">

      <!-- Header -->
      <div style="background:#1a3d2b;padding:18px 28px;display:flex;align-items:center">
        <span style="font-size:22px;margin-right:10px">🥚</span>
        <span style="color:#fff;font-weight:700;font-size:16px">QC Harvest Agent</span>
        <span style="color:rgba(255,255,255,.5);font-size:13px;margin-left:6px">/ El Mazraa · Groupe Poulina</span>
      </div>

      <!-- Body -->
      <div style="background:#fff;border:1px solid #e0ddd6;margin:16px;border-radius:8px;padding:24px 28px">

        {review_banner}

        <p style="font-size:15px;margin-bottom:20px">
          Bonjour <strong>{TECH_NAME}</strong>,<br>
          Un œuf a été analysé et validé par le pipeline IA. Voici le rapport.
        </p>

        <!-- Grade badge -->
        <div style="text-align:center;margin-bottom:20px">
          <span style="display:inline-block;padding:8px 28px;border-radius:6px;background:{grade_color}22;color:{grade_color};border:1px solid {grade_color}55;font-size:2.2rem;font-weight:700;font-family:monospace">
            {grade}
          </span>
        </div>

        <!-- Details table -->
        <table style="width:100%;border-collapse:collapse">
          <tr>
            <td style="padding:8px 0;color:#6b6b60;font-size:13px;border-bottom:1px solid #e0ddd6">Lot ID</td>
            <td style="padding:8px 0;font-weight:600;font-size:13px;border-bottom:1px solid #e0ddd6;font-family:monospace">{lot_id}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#6b6b60;font-size:13px;border-bottom:1px solid #e0ddd6">Grade final</td>
            <td style="padding:8px 0;font-weight:700;font-size:13px;border-bottom:1px solid #e0ddd6;color:{grade_color}">{grade}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#6b6b60;font-size:13px;border-bottom:1px solid #e0ddd6">Destination</td>
            <td style="padding:8px 0;font-weight:600;font-size:13px;border-bottom:1px solid #e0ddd6">{destination}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#6b6b60;font-size:13px;border-bottom:1px solid #e0ddd6">Confiance IA</td>
            <td style="padding:8px 0;font-weight:600;font-size:13px;border-bottom:1px solid #e0ddd6">{round(confidence*100)}%</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#6b6b60;font-size:13px;border-bottom:1px solid #e0ddd6">Source grading</td>
            <td style="padding:8px 0;font-size:13px;border-bottom:1px solid #e0ddd6;font-family:monospace">{grading_source}</td>
          </tr>
          {partner_html}
          <tr>
            <td style="padding:8px 0;color:#6b6b60;font-size:13px">Date analyse</td>
            <td style="padding:8px 0;font-size:13px;font-family:monospace">{datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</td>
          </tr>
        </table>

        <!-- Defects -->
        <div style="margin-top:16px">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#6b6b60;margin-bottom:6px">Défauts détectés</div>
          {defects_html}
        </div>

        <!-- PDF link -->
        {"<div style='margin-top:18px;padding:12px 16px;background:#d8ede3;border-radius:6px;font-size:13px;color:#1a3d2b'><strong>📄 Rapport PDF</strong> joint à cet email.</div>" if pdf_path and os.path.exists(pdf_path) else ""}

      </div>

      <!-- Footer -->
      <div style="padding:14px 28px;font-size:11px;color:#9b9b8e;text-align:center">
        Egg-Agent · El Mazraa · Groupe Poulina — Système automatique de traçabilité<br>
        EU 2023/2465 + INNORPI — Ne pas répondre à cet email.
      </div>
    </div>"""

    # ── Log dans le terminal (toujours) ──────────────────────
    print("\n" + "="*60)
    print(f"📧 EMAIL TECHNICIEN [{TECH_EMAIL or 'non configuré'}]")
    print(f"   Sujet  : {subject}")
    print(f"   Lot    : {lot_id} — Grade {grade}")
    print(f"   Dest   : {destination}")
    print(f"   Conf   : {round(confidence*100)}%")
    if partner_name:
        print(f"   Partner: {partner_name} — {allocation_notes}")
    print("="*60 + "\n")

    if not SMTP_ENABLED:
        logger.info("[technician_mailer] SMTP non configuré — email loggé uniquement")
        return True

    # ── Envoi réel ────────────────────────────────────────────
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = SMTP_USER
        msg["To"]      = TECH_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html"))

        # Joindre le PDF si disponible
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                part = MIMEBase("application", "pdf")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(pdf_path)}"')
            msg.attach(part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, TECH_EMAIL, msg.as_string())

        logger.info(f"[technician_mailer] ✅ Email envoyé à {TECH_EMAIL}")
        return True

    except Exception as e:
        logger.error(f"[technician_mailer] ❌ Échec envoi : {e}")
        return False
