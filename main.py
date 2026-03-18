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

from crypto_utils import gerar_carteira, verificar_pagamento_pol, calcular_pol_necessario, varrer_carteira
from email_utils import enviar_email_confirmacao, enviar_email_pagamento, enviar_email_deploy
#from incus_client import chamar_agent_banana_pi
from log_manager import registrar_log

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

MINHA_CARTEIRA_PRINCIPAL = os.getenv("MINHA_CARTEIRA_PRINCIPAL")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
PRECO_PROMO_USD = 0.10

security = HTTPBasic()

def verificar_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, os.getenv("OPS_USER", "admin"))
    correct_password = secrets.compare_digest(credentials.password, os.getenv("OPS_PASS", "admin"))
    if not (correct_username and correct_password):
        registrar_log("TENTATIVA_INVASAO", f"User tentado: {credentials.username}", "WARNING")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acesso Restrito ao Operador",
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

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    msg = request.query_params.get("msg", "")
    preco_pol_agora = calcular_pol_necessario(PRECO_PROMO_USD)
    return templates.TemplateResponse("index.html", {
        "request": request, "msg": msg, "preco_usd": PRECO_PROMO_USD,
        "preco_pol": preco_pol_agora, "vagas_disponiveis": 12, "total_slots": 50
    })

@app.get("/admin/approve/{email}")
async def admin_approve(email: str):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE users SET conf = True WHERE email = ?", (email,))
    conn.commit()
    conn.close()
    registrar_log("USER_APROVADO", f"Autorizado via URL admin", "INFO", email)
    return {"message": f"User {email} authorized."}

@app.post("/registar")
async def registar(background_tasks: BackgroundTasks, email: str = Form(...), pw: str = Form(...)):
    token = str(uuid.uuid4())
    conn = sqlite3.connect("db.sqlite")
    try:
        conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (email, pw, False, token))
        conn.commit()
        link = f"{BASE_URL}/confirmar/{token}" 
        background_tasks.add_task(enviar_email_confirmacao, email, link)
        registrar_log("NOVO_CADASTRO", "Usuário criado. Aguardando confirmação.", "INFO", email)
    except sqlite3.IntegrityError:
        return {"erro": "Email exists"}
    finally:
        conn.close()
    return RedirectResponse(url="/?msg=Check+email+to+activate#login", status_code=303)

@app.get("/confirmar/{token}")
async def confirmar(token: str):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE users SET conf = True WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    registrar_log("EMAIL_CONFIRMADO", f"Token usado: {token[:8]}...", "SUCCESS")
    return RedirectResponse(url="/?msg=Confirmed!#login", status_code=303)

@app.post("/login")
async def login(response: Response, email: str = Form(...), pw: str = Form(...)):
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT conf FROM users WHERE email=? AND pw=?", (email, pw)).fetchone()
    conn.close()
    if not user or not user[0]: 
        registrar_log("FALHA_LOGIN", "Credenciais incorretas ou não confirmado.", "WARNING", email)
        return RedirectResponse(url="/?msg=Account+not+confirmed+or+wrong+password#login", status_code=303)
    
    registrar_log("LOGIN_SUCESSO", "Sessão iniciada no dashboard.", "INFO", email)
    response = RedirectResponse(url="/dash", status_code=303)
    response.set_cookie(key="sessao", value=email)
    return response

@app.get("/dash", response_class=HTMLResponse)
async def dash(request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    preco_pol_agora = calcular_pol_necessario(PRECO_PROMO_USD)
    
    conn = sqlite3.connect("db.sqlite")
    pedidos_db = conn.execute("SELECT id, carteira, status, preco_pol FROM vps WHERE email=?", (email,)).fetchall()
    conn.close()
    
    pedidos = [(p[0], p[1], p[2], p[3], None, int(p[3] * (10**18))) for p in pedidos_db]
        
    return templates.TemplateResponse("dash.html", {
        "request": request, "pedidos": pedidos, 
        "preco_usd": PRECO_PROMO_USD, "preco_pol": preco_pol_agora 
    })

@app.post("/comprar")
async def comprar(request: Request, bg_tasks: BackgroundTasks):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    
    id_pedido = "vps-" + str(uuid.uuid4())[:8]
    endereco, chave_privada = gerar_carteira()
    preco_travado_pol = calcular_pol_necessario(PRECO_PROMO_USD)
    
    conn = sqlite3.connect("db.sqlite")
    conn.execute("INSERT INTO vps VALUES (?, ?, ?, ?, ?, ?)", (id_pedido, email, endereco, chave_privada, "PENDING PAYMENT", preco_travado_pol))
    conn.commit()
    conn.close()
    
    registrar_log("GEROU_PEDIDO", f"VPS {id_pedido} aguardando {preco_travado_pol} POL na carteira {endereco}", "INFO", email)
    bg_tasks.add_task(vigiar_e_implementar, id_pedido, endereco, email, preco_travado_pol)
    return RedirectResponse(url="/dash", status_code=303)

@app.post("/apagar_vps/{id_vps}")
async def apagar_vps(id_vps: str, request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    conn = sqlite3.connect("db.sqlite")
    conn.execute("DELETE FROM vps WHERE id=? AND email=?", (id_vps, email))
    conn.commit()
    conn.close()
    registrar_log("DELETOU_PEDIDO", f"Instância {id_vps} removida pelo usuário.", "INFO", email)
    return RedirectResponse(url="/dash", status_code=303)

async def vigiar_e_implementar(id_pedido, endereco, email, valor_esperado_pol):
    for _ in range(120):
        if verificar_pagamento_pol(endereco, valor_esperado_pol):
            conn = sqlite3.connect("db.sqlite")
            conn.execute("UPDATE vps SET status = 'ATIVA' WHERE id = ?", (id_pedido,))
            cursor = conn.execute("SELECT chave_privada FROM vps WHERE id = ?", (id_pedido,))
            chave_privada = cursor.fetchone()[0]
            conn.commit()
            conn.close()
            
            registrar_log("PAGAMENTO_RECEBIDO", f"Valor: {valor_esperado_pol} POL na VPS {id_pedido}", "SUCCESS", email)

            if MINHA_CARTEIRA_PRINCIPAL:
                tx_hash = varrer_carteira(endereco, chave_privada, MINHA_CARTEIRA_PRINCIPAL)
                if tx_hash:
                    registrar_log("SWEEP_OK", f"https://polygonscan.com/tx/0x{tx_hash}", "SUCCESS", email)
                else:
                    registrar_log("SWEEP_FALHOU", "Erro ao raspar saldo.", "ERROR", email)

            enviar_email_pagamento(email, id_pedido, valor_esperado_pol)

            try:
                resultado = await chamar_agent_banana_pi(id_pedido)
                if resultado.get("sucesso"):
                    registrar_log("DEPLOY_OK", f"Node provisionado: {resultado.get('ip')}", "SUCCESS", email)
                    enviar_email_deploy(email, id_pedido, resultado.get("ip"), resultado.get("senha"))
                else:
                    registrar_log("DEPLOY_FALHOU", f"API Banana Pi retornou erro.", "ERROR", email)
            except Exception as e:
                registrar_log("BRIDGE_ERROR", f"Falha de conexão com Banana Pi: {e}", "CRITICAL", email)
            return
            
        await asyncio.sleep(30)

# ==========================================
# PAINEL DO ADMINISTRADOR
# ==========================================
@app.get("/ops", response_class=HTMLResponse)
async def painel_ops(request: Request, admin: str = Depends(verificar_admin)):
    conn = sqlite3.connect("db.sqlite")
    usuarios = conn.execute("SELECT email, conf FROM users").fetchall()
    vps_ativas = conn.execute("SELECT id, email, carteira, status, preco_pol FROM vps").fetchall()
    conn.close()

    conn_log = sqlite3.connect("logs.sqlite")
    logs = conn_log.execute("SELECT timestamp, nivel, evento, detalhes, email FROM system_logs ORDER BY id DESC LIMIT 50").fetchall()
    conn_log.close()

    return templates.TemplateResponse("ops.html", {
        "request": request,
        "usuarios": usuarios,
        "vps": vps_ativas,
        "logs": logs
    })