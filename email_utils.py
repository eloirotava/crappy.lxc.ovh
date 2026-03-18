import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

load_dotenv()

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

# 🚨 A MÁGICA AQUI: Pega o SENDER_EMAIL do .env
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USER)

def enviar_email_base(destinatario, assunto, corpo_html):
    """Função base que conecta no SMTP do Brevo e envia o e-mail"""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[⚠️] E-mail não enviado para {destinatario}: Credenciais SMTP ausentes no .env")
        return

    msg = MIMEMultipart()
    
    # 🚨 AGORA USA O SENDER CORRETO!
    msg['From'] = f"Crappy LXC <{SENDER_EMAIL}>"
    msg['To'] = destinatario
    msg['Subject'] = assunto

    # Template visual Hacker/Terminal
    template_hacker = f"""
    <html>
    <body style="background-color: #0a0a0a; color: #00ff00; font-family: 'Courier New', Courier, monospace; padding: 20px;">
        <div style="border: 1px dashed #00ff00; padding: 20px; max-width: 600px; margin: 0 auto; background-color: #050505;">
            <p style="margin: 0;"><b>root@crappy-lxc:~#</b> mail -s "{assunto}" {destinatario}</p>
            <hr style="border: 0; border-bottom: 1px dashed #333; margin: 20px 0;">
            {corpo_html}
            <hr style="border: 0; border-bottom: 1px dashed #333; margin: 20px 0;">
            <p style="color: #aaa; font-size: 0.8em;">> EOF (End Of File)</p>
            <p style="color: #555; font-size: 0.7em;">Node: BananaPi-M2-Zero | Region: Your-Living-Room</p>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(template_hacker, 'html'))

    try:
        # Conexão segura via TLS
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        print(f"[📧] BREVO: E-mail '{assunto}' enviado com sucesso para {destinatario}!")
    except Exception as e:
        print(f"[❌] Erro ao enviar e-mail via Brevo: {e}")

# --- Funções Específicas ---

def enviar_email_confirmacao(destinatario, link_confirmacao):
    assunto = "[ACTION] Confirm Account Access"
    html = f"""
    <p>Authentication request detected for this node.</p>
    <p>To authorize, run the following command (click the link):</p>
    <p><a href="{link_confirmacao}" style="color: yellow; text-decoration: none; border: 1px solid yellow; padding: 10px; display: inline-block;">./authorize_user.sh</a></p>
    """
    enviar_email_base(destinatario, assunto, html)

def enviar_email_pagamento(destinatario, id_pedido, valor_pol):
    assunto = f"[OK] Payment Received: {id_pedido}"
    html = f"""
    <p style="color: yellow;">> BLOCKCHAIN STATUS: CONFIRMED</p>
    <p>Received: <b>{valor_pol} POL</b></p>
    <p>Automated sweep performed. Provisioning your container now...</p>
    """
    enviar_email_base(destinatario, assunto, html)

def enviar_email_deploy(destinatario, id_pedido, ip, senha):
    assunto = f"[LIVE] Your LXC {id_pedido} is Ready"
    html = f"""
    <h3 style="color: #00ff00;">> DEPLOYMENT COMPLETE</h3>
    <p><b>IPv6:</b> <span style="color: white;">{ip}</span></p>
    <p><b>Root Pass:</b> <span style="color: #ff5555;">{senha}</span></p>
    <p style="margin-top: 20px;">Connect now:</p>
    <code style="color: yellow;">ssh root@{ip}</code>
    """
    enviar_email_base(destinatario, assunto, html)