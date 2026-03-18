from fastapi import FastAPI, Request, Form, BackgroundTasks, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import sqlite3
import uuid
import time
from crypto_utils import gerar_carteira, verificar_pagamento_pol, calcular_pol_necessario
from email_utils import enviar_email
from incus_client import chamar_agent_banana_pi
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")
PRECO_USD_PROMO = 0.10

def init_db():
    conn = sqlite3.connect("db.sqlite")
    conn.execute('''CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, pw TEXT, conf BOOLEAN, token TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS vps (id TEXT PRIMARY KEY, email TEXT, carteira TEXT, status TEXT, preco_pol REAL)''')
    conn.commit()
    conn.close()

init_db()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse("index.html", {"request": request, "msg": msg})

@app.post("/registar")
async def registar(background_tasks: BackgroundTasks, email: str = Form(...), pw: str = Form(...)):
    token = str(uuid.uuid4())
    conn = sqlite3.connect("db.sqlite")
    try:
        conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (email, pw, False, token))
        conn.commit()
        # Ajuste para localhost temporariamente
        link = f"http://127.0.0.1:8000/confirmar/{token}" 
        html = f"Clica para confirmar o bunker: <a href='{link}'>{link}</a>"
        background_tasks.add_task(enviar_email, email, "Confirma o teu Bunker", html)
    except sqlite3.IntegrityError:
        return {"erro": "Email já existe"}
    finally:
        conn.close()
    return RedirectResponse(url="/?msg=Verifica+o+teu+email", status_code=303)

@app.get("/confirmar/{token}")
async def confirmar(token: str):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE users SET conf = True WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/?msg=Confirmado!+Faz+Login", status_code=303)

@app.post("/login")
async def login(response: Response, email: str = Form(...), pw: str = Form(...)):
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT conf FROM users WHERE email=? AND pw=?", (email, pw)).fetchone()
    conn.close()
    
    if not user or not user[0]: 
        return RedirectResponse(url="/?msg=Invalido+ou+nao+confirmado", status_code=303)
    
    response = RedirectResponse(url="/dash", status_code=303)
    response.set_cookie(key="sessao", value=email)
    return response

@app.get("/dash", response_class=HTMLResponse)
async def dash(request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    
    preco_pol_agora = calcular_pol_necessario(PRECO_USD_PROMO)
    
    conn = sqlite3.connect("db.sqlite")
    pedidos = conn.execute("SELECT id, carteira, status, preco_pol FROM vps WHERE email=?", (email,)).fetchall()
    conn.close()
    
    return templates.TemplateResponse("dash.html", {
        "request": request, 
        "pedidos": pedidos, 
        "preco_usd": PRECO_USD_PROMO,
        "preco_pol": preco_pol_agora 
    })

@app.post("/comprar")
async def comprar(request: Request, bg_tasks: BackgroundTasks):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/")
    
    id_pedido = "vps-" + str(uuid.uuid4())[:8]
    endereco, _ = gerar_carteira()
    preco_travado_pol = calcular_pol_necessario(PRECO_USD_PROMO)
    
    conn = sqlite3.connect("db.sqlite")
    conn.execute("INSERT INTO vps VALUES (?, ?, ?, ?, ?)", (id_pedido, email, endereco, "AGUARDANDO PAGAMENTO", preco_travado_pol))
    conn.commit()
    conn.close()
    
    bg_tasks.add_task(vigiar_e_implementar, id_pedido, endereco, email, preco_travado_pol)
    return RedirectResponse(url="/dash", status_code=303)

async def vigiar_e_implementar(id_pedido, endereco, email, valor_esperado_pol):
    for _ in range(120): # Vigia por 1 hora (tentativas a cada 30s)
        if verificar_pagamento_pol(endereco, valor_esperado_pol):
            conn = sqlite3.connect("db.sqlite")
            conn.execute("UPDATE vps SET status = 'ATIVA' WHERE id = ?", (id_pedido,))
            conn.commit()
            conn.close()
            
            resultado = await chamar_agent_banana_pi(id_pedido)
            
            if resultado.get("sucesso"):
                html = f"A tua VPS está viva!<br>IP: {resultado.get('ip')}<br>Senha root: {resultado.get('senha')}"
                await enviar_email(email, "A tua Crappi VPS está online!", html)
            return
        time.sleep(30)