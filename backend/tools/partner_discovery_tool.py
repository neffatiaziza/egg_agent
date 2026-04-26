# HUMAN-IN-THE-LOOP: no external contact without technician approval
import os
import json
import logging
from datetime import datetime
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

@tool("partner_discovery_tool")
async def partner_discovery_tool(grade: str, size: str, quantity: int, price_tnd: float) -> dict:
    """
    Finds potential buyers in Tunisia for surplus eggs when no active order exists.
    Extracts leads using Tavily search and Groq LLM.
    Sends an email to the technician for approval.
    """
    leads = []
    total_value_tnd = quantity * price_tnd
    technician_email = os.getenv("TECHNICIAN_EMAIL", "")
    technician_name = os.getenv("TECHNICIAN_NAME", "Technician")
    email_sent = False

    # 1. Search Tavily
    import httpx
    try:
        tavily_api_key = os.getenv("TAVILY_API_KEY")
        if tavily_api_key:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_api_key,
                        "query": f"entreprises agroalimentaire patisserie supermarché Tunisie achat oeufs",
                        "search_depth": "advanced",
                        "max_results": 5
                    },
                    timeout=15.0
                )
                search_results = res.json().get("results", [])
                search_context = "\n".join([f"- {r.get('title')}: {r.get('content')}" for r in search_results])
        else:
            search_context = "No Tavily API key."
    except Exception as e:
        logger.error(f"[partner_discovery] Tavily error: {e}")
        search_context = f"Error: {e}"

    # 2. Extract leads using Groq
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import HumanMessage
        llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=os.getenv("GROQ_API_KEY"),
            temperature=0
        )
        prompt = f"""
        Extract potential egg buyers from Tunisia from the following search results.
        Format as JSON list of objects with exactly these keys: name, sector, city, email, phone.
        Return ONLY valid JSON. If no leads are found, return [].
        
        Search Results:
        {search_context}
        """
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        leads = json.loads(raw.strip())
        if not isinstance(leads, list):
            leads = []
    except Exception as e:
        logger.error(f"[partner_discovery] LLM parsing error: {e}")
        leads = []

    # 3. Send email to Technician
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        SMTP_HOST     = os.getenv("SMTP_HOST",        "smtp.gmail.com")
        SMTP_PORT     = int(os.getenv("SMTP_PORT",    "587"))
        SMTP_USER     = os.getenv("SMTP_USER",        "")
        SMTP_PASSWORD = os.getenv("SMTP_PASSWORD",    "")

        if SMTP_USER and SMTP_PASSWORD and technician_email:
            leads_html = ""
            for lead in leads:
                email_link = f"<a href='mailto:{lead.get('email')}'>{lead.get('email')}</a>" if lead.get('email') and lead.get('email') != 'non trouvé' else "non trouvé"
                leads_html += f"""
                <tr>
                    <td style="padding:8px;border-bottom:1px solid #e0ddd6;">{lead.get('name', 'N/A')}</td>
                    <td style="padding:8px;border-bottom:1px solid #e0ddd6;">{lead.get('sector', 'N/A')}</td>
                    <td style="padding:8px;border-bottom:1px solid #e0ddd6;">{lead.get('city', 'N/A')}</td>
                    <td style="padding:8px;border-bottom:1px solid #e0ddd6;">{email_link}</td>
                    <td style="padding:8px;border-bottom:1px solid #e0ddd6;">{lead.get('phone', 'N/A')}</td>
                </tr>
                """

            html = f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#f4f2ed;padding:16px;">
                <div style="background:#1a3d2b;color:white;padding:16px;">
                    <h2 style="margin:0;">Egg Agent — Rapport de Surplus</h2>
                    <p style="margin:4px 0 0;font-size:13px;color:rgba(255,255,255,0.8);">Pour: {technician_name} | {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>
                
                <div style="background:white;padding:16px;margin-top:16px;border-radius:8px;">
                    <h3 style="margin-top:0;">Résumé du Surplus</h3>
                    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                        <tr style="background:#f9f9f9;">
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Grade</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Taille</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Quantité</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Prix unitaire</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;background:#d8ede3;color:#1a3d2b;">Valeur totale (TND)</th>
                        </tr>
                        <tr>
                            <td style="padding:8px;border-bottom:1px solid #eee;">{grade}</td>
                            <td style="padding:8px;border-bottom:1px solid #eee;">{size}</td>
                            <td style="padding:8px;border-bottom:1px solid #eee;">{quantity}</td>
                            <td style="padding:8px;border-bottom:1px solid #eee;">{price_tnd:.2f}</td>
                            <td style="padding:8px;border-bottom:1px solid #eee;background:#eef7f2;font-weight:bold;color:#1a3d2b;">{total_value_tnd:.2f}</td>
                        </tr>
                    </table>

                    <div style="border-left:4px solid #b7791f;background:#fef3cd;padding:12px;margin-bottom:20px;">
                        <strong style="color:#b7791f;">Aucun partenaire contacté — action requise de votre part</strong>
                    </div>

                    <h3>Pistes potentielles trouvées ({len(leads)})</h3>
                    <table style="width:100%;border-collapse:collapse;font-size:13px;">
                        <tr style="background:#f9f9f9;">
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Entreprise</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Secteur</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Ville</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Email</th>
                            <th style="padding:8px;text-align:left;border-bottom:2px solid #ddd;">Téléphone</th>
                        </tr>
                        {leads_html}
                    </table>
                </div>
                
                <div style="margin-top:20px;text-align:center;font-size:12px;color:#666;">
                    Généré automatiquement par QC Harvest Agent · El Mazraa · Groupe Poulina
                </div>
            </div>
            """

            msg = MIMEMultipart("alternative")
            msg["From"] = SMTP_USER
            msg["To"] = technician_email
            msg["Subject"] = f"[Egg Agent] Surplus détecté — Grade {grade} / {size} — {quantity} unités"
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, technician_email, msg.as_string())
            
            email_sent = True
    except Exception as e:
        logger.error(f"[partner_discovery] Email failed: {e}")

    # 4. Log DB event
    try:
        from backend.services.notification_service import log_discovery_event
        log_discovery_event(grade, size, quantity, leads, price_tnd)
    except Exception as e:
        logger.error(f"[partner_discovery] Logging DB failed: {e}")

    return {
        "status": "success",
        "technician_email": technician_email,
        "leads_found": len(leads),
        "leads": leads,
        "surplus_value_tnd": total_value_tnd,
        "email_sent": email_sent,
        "message": f"Found {len(leads)} leads. Email sent: {email_sent}."
    }
