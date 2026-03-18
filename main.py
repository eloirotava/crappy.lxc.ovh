from fastapi import FastAPI, Request, Form, BackgroundTasks, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import sqlite3
import uuid
import os
import asyncio
from dotenv import load_dotenv

from crypto_utils import gerar_carteira, verificar_pagamento_pol, calcular_pol_necessario, varrer_carteira
from email_utils import enviar_email
from incus_client import chamar_agent_banana_pi

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==========================================
# ⚠️ COLE AQUI O SEU ENDEREÇO EVM (Começa com 0x)
MINHA_CARTEIRA_PRINCIPAL = os.getenv("MINHA_CARTEIRA_PRINCIPAL")
# ==========================================

PRECO_TESTE_POL = calcular_pol_necessario(0.10)

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
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "msg": msg,
        "preco_usd": 0.10,
        "preco_pol": PRECO_TESTE_POL, 
        "vagas_disponiveis": 12,
        "total_slots": 50
    })

@app.get("/admin/approve/{email}")
async def admin_approve(email: str):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE users SET conf = True WHERE email = ?", (email,))
    conn.commit()
    conn.close()
    return {"message": f"User {email} is now authorized to login."}

@app.post("/registar")
async def registar(background_tasks: BackgroundTasks, email: str = Form(...), pw: str = Form(...)):
    token = str(uuid.uuid4())
    conn = sqlite3.connect("db.sqlite")
    try:
        conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (email, pw, False, token))
        conn.commit()
        print(f"\n--- NOVO REGISTRO: {email} ---")
        print(f"Link de admin para aprovar: http://127.0.0.1:8000/admin/approve/{email}")
        print("-------------------------------\n")
    except sqlite3.IntegrityError:
        return {"erro": "Email exists"}
    finally:
        conn.close()
    return RedirectResponse(url="/?msg=Check+email+or+wait+for+admin+approval#login", status_code=303)

@app.get("/confirmar/{token}")
async def confirmar(token: str):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE users SET conf = True WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/?msg=Confirmed!#login", status_code=303)

@app.post("/login")
async def login(response: Response, email: str = Form(...), pw: str = Form(...)):
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT conf FROM users WHERE email=? AND pw=?", (email, pw)).fetchone()
    conn.close()
    if not user or not user[0]: 
        return RedirectResponse(url="/?msg=Wait+for+approval+or+check+email#login", status_code=303)
    response = RedirectResponse(url="/dash", status_code=303)
    response.set_cookie(key="sessao", value=email)
    return response

@app.get("/dash", response_class=HTMLResponse)
async def dash(request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    conn = sqlite3.connect("db.sqlite")
    pedidos_db = conn.execute("SELECT id, carteira, status, preco_pol, chave_privada FROM vps WHERE email=?", (email,)).fetchall()
    conn.close()
    pedidos = []
    for p in pedidos_db:
        preco_wei = int(p[3] * (10**18))
        pedidos.append((p[0], p[1], p[2], p[3], p[4], preco_wei))
    return templates.TemplateResponse("dash.html", {"request": request, "pedidos": pedidos, "preco_usd": 0.10, "preco_pol": PRECO_TESTE_POL })

@app.post("/comprar")
async def comprar(request: Request, bg_tasks: BackgroundTasks):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    id_pedido = "vps-" + str(uuid.uuid4())[:8]
    endereco, chave_privada = gerar_carteira()
    
    conn = sqlite3.connect("db.sqlite")
    conn.execute("INSERT INTO vps VALUES (?, ?, ?, ?, ?, ?)", (id_pedido, email, endereco, chave_privada, "PENDING PAYMENT", PRECO_TESTE_POL))
    conn.commit()
    conn.close()
    
    bg_tasks.add_task(vigiar_e_implementar, id_pedido, endereco, email, PRECO_TESTE_POL)
    return RedirectResponse(url="/dash", status_code=303)

@app.post("/apagar_vps/{id_vps}")
async def apagar_vps(id_vps: str, request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    conn = sqlite3.connect("db.sqlite")
    conn.execute("DELETE FROM vps WHERE id=? AND email=?", (id_vps, email))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/dash", status_code=303)

async def vigiar_e_implementar(id_pedido, endereco, email, valor_esperado_pol):
    for _ in range(120):
        if verificar_pagamento_pol(endereco, valor_esperado_pol):
            # 1. Atualiza o DB para ATIVA
            conn = sqlite3.connect("db.sqlite")
            conn.execute("UPDATE vps SET status = 'ATIVA' WHERE id = ?", (id_pedido,))
            
            # Busca a chave privada recém-paga no banco para fazer a raspa
            cursor = conn.execute("SELECT chave_privada FROM vps WHERE id = ?", (id_pedido,))
            chave_privada = cursor.fetchone()[0]
            conn.commit()
            conn.close()
            
            # 2. CHUTA O DINHEIRO PRA SUA CARTEIRA! (Sweep)
            if MINHA_CARTEIRA_PRINCIPAL != "0xCOLE_SEU_ENDERECO_AQUI":
                varrer_carteira(endereco, chave_privada, MINHA_CARTEIRA_PRINCIPAL)
            else:
                print("[⚠️] Você esqueceu de configurar a sua carteira no main.py! O dinheiro ficou na VPS.")

            # 3. Manda rodar no Banana Pi
            resultado = await chamar_agent_banana_pi(id_pedido)
            if resultado.get("sucesso"):
                html = f"Alive! IP: {resultado.get('ip')} PW: {resultado.get('senha')}"
                await enviar_email(email, "LXC Online", html)
            return
            
        await asyncio.sleep(30)