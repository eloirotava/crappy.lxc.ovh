from fastapi import FastAPI, Request, Form, BackgroundTasks, Response, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import sqlite3
import uuid
import os
import asyncio
import secrets
from dotenv import load_dotenv

# Utilitários do projeto
from crypto_utils import gerar_carteira, verificar_pagamento_pol, calcular_pol_necessario, varrer_carteira, w3
from email_utils import enviar_email_confirmacao, enviar_email_pagamento, enviar_email_deploy
from lxc_client import chamar_agent_banana_pi
from log_manager import registrar_log

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Configurações de Ambiente
MINHA_CARTEIRA_PRINCIPAL = os.getenv("MINHA_CARTEIRA_PRINCIPAL")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
PRECO_PROMO_USD = 0.10

# Segurança do Painel /ops
security = HTTPBasic()

def verificar_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, os.getenv("OPS_USER", "admin"))
    correct_password = secrets.compare_digest(credentials.password, os.getenv("OPS_PASS", "admin"))
    if not (correct_username and correct_password):
        registrar_log("TENTATIVA_INVASAO", f"User: {credentials.username}", "WARNING")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acesso Restrito",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def init_db():
    conn = sqlite3.connect("db.sqlite")
    conn.execute('''CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, pw TEXT, conf BOOLEAN, token TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS vps (id TEXT PRIMARY KEY, email TEXT, carteira TEXT, chave_privada TEXT, status TEXT, preco_pol REAL)''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# ROTAS PÚBLICAS (NAVEGAÇÃO)
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/hosting", response_class=HTMLResponse)
async def hosting_page(request: Request):
    return templates.TemplateResponse("hosting.html", {"request": request})

@app.get("/vps", response_class=HTMLResponse)
async def vps_page(request: Request):
    p_pol = calcular_pol_necessario(PRECO_PROMO_USD)
    return templates.TemplateResponse("vps.html", {"request": request, "preco_usd": PRECO_PROMO_USD, "preco_pol": p_pol})

@app.get("/tos", response_class=HTMLResponse)
async def tos_page(request: Request):
    return templates.TemplateResponse("tos.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse("login.html", {"request": request, "msg": msg})

# ==========================================
# AUTENTICAÇÃO
# ==========================================

@app.post("/registar")
async def registar(bg_tasks: BackgroundTasks, email: str = Form(...), pw: str = Form(...), tos: str = Form(None)):
    if not tos:
        return RedirectResponse(url="/login?msg=You+must+accept+the+ToS", status_code=303)
    token = str(uuid.uuid4())
    conn = sqlite3.connect("db.sqlite")
    try:
        conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (email, pw, False, token))
        conn.commit()
        bg_tasks.add_task(enviar_email_confirmacao, email, f"{BASE_URL}/confirmar/{token}")
        registrar_log("NOVO_USER", "Conta criada.", "INFO", email)
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/login?msg=Email+already+exists", status_code=303)
    finally: conn.close()
    return RedirectResponse(url="/login?msg=Check+your+email", status_code=303)

@app.get("/confirmar/{token}")
async def confirmar(token: str):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE users SET conf = True WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/login?msg=Account+Confirmed!", status_code=303)

@app.post("/login")
async def login(response: Response, email: str = Form(...), pw: str = Form(...)):
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT conf FROM users WHERE email=? AND pw=?", (email, pw)).fetchone()
    conn.close()
    if not user or not user[0]: 
        return RedirectResponse(url="/login?msg=Invalid+credentials+or+unconfirmed", status_code=303)
    res = RedirectResponse(url="/dash", status_code=303)
    res.set_cookie(key="sessao", value=email)
    return res

# ==========================================
# PAINEL DO CLIENTE E VPS
# ==========================================

@app.get("/dash", response_class=HTMLResponse)
async def dash(request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    conn = sqlite3.connect("db.sqlite")
    # Esconde as deletadas do Dashboard do cliente
    pedidos = conn.execute("SELECT id, carteira, status, preco_pol FROM vps WHERE email=? AND status != 'DELETED'", (email,)).fetchall()
    conn.close()
    return templates.TemplateResponse("dash.html", {"request": request, "pedidos": pedidos})

@app.post("/comprar")
async def comprar(request: Request, bg_tasks: BackgroundTasks):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    id_pedido = "vps-" + str(uuid.uuid4())[:8]
    endereco, chave_privada = gerar_carteira()
    p_pol = calcular_pol_necessario(PRECO_PROMO_USD)
    conn = sqlite3.connect("db.sqlite")
    conn.execute("INSERT INTO vps VALUES (?, ?, ?, ?, ?, ?)", (id_pedido, email, endereco, chave_privada, "PENDING PAYMENT", p_pol))
    conn.commit()
    conn.close()
    bg_tasks.add_task(vigiar_e_implementar, id_pedido, endereco, email, p_pol)
    return RedirectResponse(url="/dash", status_code=303)

@app.post("/apagar_vps/{id_vps}")
async def apagar_vps(id_vps: str, request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    conn = sqlite3.connect("db.sqlite")
    # 🛡️ SOFT DELETE: Preserva a chave privada no banco
    conn.execute("UPDATE vps SET status = 'DELETED' WHERE id=? AND email=?", (id_vps, email))
    conn.commit()
    conn.close()
    registrar_log("SOFT_DELETE", f"User removeu UI da {id_vps}", "INFO", email)
    return RedirectResponse(url="/dash", status_code=303)

# ==========================================
# LOGICA DE VIGIA E DEPLOY (O CÉREBRO)
# ==========================================

async def vigiar_e_implementar(id_pedido, endereco, email, valor_esperado_pol):
    for _ in range(120): # Monitora por 1 hora
        # 🛡️ CHECAGEM DE INTERRUPÇÃO: Se já foi ativa manualmente ou deletada, para a vigia.
        conn = sqlite3.connect("db.sqlite")
        status_vps = conn.execute("SELECT status FROM vps WHERE id = ?", (id_pedido,)).fetchone()[0]
        conn.close()

        if status_vps in ["ATIVA", "BANNED", "DELETED"]:
            registrar_log("VIGIA_STOP", f"Task encerrada para {id_pedido} (Status: {status_vps})", "INFO", email)
            return

        # Verifica se o pagamento caiu
        if verificar_pagamento_pol(endereco, valor_esperado_pol):
            conn = sqlite3.connect("db.sqlite")
            cursor = conn.execute("SELECT chave_privada FROM vps WHERE id = ?", (id_pedido,))
            chave_privada = cursor.fetchone()[0]
            conn.execute("UPDATE vps SET status = 'ATIVA' WHERE id = ?", (id_pedido,))
            conn.commit()
            conn.close()
            
            registrar_log("PAGO", f"VPS {id_pedido} recebida.", "SUCCESS", email)

            # Sweep (Raspa o dinheiro com Gás Turbo)
            if MINHA_CARTEIRA_PRINCIPAL:
                varrer_carteira(endereco, chave_privada, MINHA_CARTEIRA_PRINCIPAL)

            # Deploy no LXC
            resultado = await chamar_agent_banana_pi(id_pedido)
            if resultado.get("sucesso"):
                registrar_log("DEPLOY_OK", f"IP: {resultado.get('ip')}", "SUCCESS", email)
                enviar_email_deploy(email, id_pedido, resultado.get("ip"), resultado.get("senha"))
            return
        
        await asyncio.sleep(30)

# ==========================================
# PAINEL DE OPERAÇÕES (/ops)
# ==========================================

@app.get("/ops", response_class=HTMLResponse)
async def painel_ops(request: Request, admin: str = Depends(verificar_admin)):
    msg = request.query_params.get("msg", "")
    conn = sqlite3.connect("db.sqlite")
    usuarios = conn.execute("SELECT email, conf FROM users").fetchall()
    # No OPS, mostramos TUDO, inclusive as DELETED
    vps_geral = conn.execute("SELECT id, email, status, carteira FROM vps").fetchall()
    conn.close()
    
    conn_log = sqlite3.connect("logs.sqlite")
    logs = conn_log.execute("SELECT timestamp, nivel, evento, detalhes, email FROM system_logs ORDER BY id DESC LIMIT 30").fetchall()
    conn_log.close()

    try: gas = f"{w3.from_wei(w3.eth.gas_price, 'gwei'):.1f} Gwei"
    except: gas = "OFFLINE"

    return templates.TemplateResponse("ops.html", {
        "request": request, "usuarios": usuarios, "vps": vps_geral, "logs": logs, "gas": gas, "msg": msg
    })

@app.post("/ops/force_activate/{id_vps}")
async def force_activate(id_vps: str, bg_tasks: BackgroundTasks, admin: str = Depends(verificar_admin)):
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT email FROM vps WHERE id = ?", (id_vps,)).fetchone()
    if user:
        conn.execute("UPDATE vps SET status = 'MANUAL_START' WHERE id = ?", (id_vps,))
        conn.commit()
        # Dispara o deploy sem precisar de pagamento
        bg_tasks.add_task(processar_ativacao_manual, id_vps, user[0])
    conn.close()
    registrar_log("FORCE_START", f"Admin {admin} forçou a ativação da {id_vps}", "WARNING", user[0] if user else "System")
    return RedirectResponse(url="/ops?msg=Manual+Deploy+Started", status_code=303)

async def processar_ativacao_manual(id_vps, email):
    res = await chamar_agent_banana_pi(id_vps)
    if res.get("sucesso"):
        conn = sqlite3.connect("db.sqlite")
        conn.execute("UPDATE vps SET status = 'ATIVA' WHERE id = ?", (id_vps,))
        conn.commit()
        conn.close()
        enviar_email_deploy(email, id_vps, res.get("ip"), res.get("senha"))
        registrar_log("FORCE_OK", f"LXC Ativo: {id_vps}", "SUCCESS", email)

@app.post("/ops/nuke/{id_vps}")
async def nuke_vps(id_vps: str, admin: str = Depends(verificar_admin)):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE vps SET status = 'BANNED' WHERE id = ?", (id_vps,))
    conn.commit()
    conn.close()
    registrar_log("NUKE", f"VPS {id_vps} BANIDA.", "CRITICAL", admin)
    return RedirectResponse(url="/ops?msg=Nuked", status_code=303)