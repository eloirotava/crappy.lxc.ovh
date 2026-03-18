import httpx
import os
from dotenv import load_dotenv

load_dotenv()

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "admin@lxc.ovh")

async def enviar_email(destino, assunto, html_content):
    url = "https://api.brevo.com/v3/smtp/email"
    payload = {
        "sender": {"name": "Crappi LXC", "email": SENDER_EMAIL},
        "to": [{"email": destino}],
        "subject": assunto,
        "htmlContent": html_content
    }
    headers = {
        "accept": "application/json", 
        "content-type": "application/json", 
        "api-key": BREVO_API_KEY
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers)