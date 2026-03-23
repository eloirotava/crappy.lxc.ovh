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
import httpx
import ipaddress
from dotenv import load_dotenv

from crypto_utils import gerar_carteira, verificar_pagamento_pol, calcular_pol_necessario, varrer_carteira, w3
from email_utils import enviar_email_confirmacao, enviar_email_pagamento, enviar_email_deploy, enviar_email_recuperacao
from lxc_client import chamar_agent_banana_pi, controlar_vps
from log_manager import registrar_log

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

MINHA_CARTEIRA_PRINCIPAL = os.getenv("MINHA_CARTEIRA_PRINCIPAL")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")

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
    
    conn.execute('''CREATE TABLE IF NOT EXISTS nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        nome TEXT, url_agente TEXT, token_agente TEXT, 
        arquitetura TEXT, preco_usd REAL, limite_vps INTEGER, ativo INTEGER DEFAULT 1)''')
        
    conn.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT, assunto TEXT, mensagem TEXT, resposta TEXT,
        status TEXT DEFAULT 'OPEN', data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(users)")
    colunas_users = [col[1] for col in cursor.fetchall()]
    if 'reset_token' not in colunas_users: conn.execute("ALTER TABLE users ADD COLUMN reset_token TEXT")

    cursor.execute("PRAGMA table_info(vps)")
    colunas_vps = [col[1] for col in cursor.fetchall()]
    if 'node_id' not in colunas_vps: conn.execute("ALTER TABLE vps ADD COLUMN node_id INTEGER DEFAULT 1")
    if 'validade' not in colunas_vps:
        conn.execute("ALTER TABLE vps ADD COLUMN validade TEXT")
        conn.execute("UPDATE vps SET validade = datetime('now', '+30 days') WHERE status = 'ATIVA'")
    if 'ipv4' not in colunas_vps:
        conn.execute("ALTER TABLE vps ADD COLUMN ipv4 TEXT")
        conn.execute("ALTER TABLE vps ADD COLUMN ipv6 TEXT")
        
    cursor.execute("PRAGMA table_info(nodes)")
    colunas_nodes = [col[1] for col in cursor.fetchall()]
    if 'ram_mb' not in colunas_nodes:
        conn.execute("ALTER TABLE nodes ADD COLUMN ram_mb INTEGER DEFAULT 64")
        conn.execute("ALTER TABLE nodes ADD COLUMN swap_mb INTEGER DEFAULT 32")
        conn.execute("ALTER TABLE nodes ADD COLUMN disk_mb INTEGER DEFAULT 1024")
        conn.execute("ALTER TABLE nodes ADD COLUMN cpu_fraction TEXT DEFAULT '20%'")
    if 'preco_renew_usd' not in colunas_nodes:
        conn.execute("ALTER TABLE nodes ADD COLUMN preco_renew_usd REAL DEFAULT 0.10")
        conn.execute("ALTER TABLE nodes ADD COLUMN preco_ano_usd REAL DEFAULT 1.00")
        conn.execute("UPDATE nodes SET preco_renew_usd = preco_usd, preco_ano_usd = preco_usd * 10")
    if 'descricao_hardware' not in colunas_nodes:
        conn.execute("ALTER TABLE nodes ADD COLUMN descricao_hardware TEXT DEFAULT 'SBC Genérico'")
        conn.execute("ALTER TABLE nodes ADD COLUMN cpu_core TEXT DEFAULT '0'")
    if 'ordem' not in colunas_nodes:
        conn.execute("ALTER TABLE nodes ADD COLUMN ordem INTEGER DEFAULT 0")
        
    # COLUNAS DE REDE E SCRIPT NO NODE
    if 'ipv4_base' not in colunas_nodes:
        conn.execute("ALTER TABLE nodes ADD COLUMN ipv4_base TEXT DEFAULT '10.0.0.'")
        conn.execute("ALTER TABLE nodes ADD COLUMN ipv4_cidr TEXT DEFAULT '32'")
        conn.execute("ALTER TABLE nodes ADD COLUMN ipv4_gw TEXT DEFAULT '10.0.0.1'")
        conn.execute("ALTER TABLE nodes ADD COLUMN ipv6_base TEXT DEFAULT '2804:14d:7e89:41a0::'")
        conn.execute("ALTER TABLE nodes ADD COLUMN ipv6_cidr TEXT DEFAULT '64'")
        conn.execute("ALTER TABLE nodes ADD COLUMN ipv6_gw TEXT DEFAULT 'fe80::1'")
    if 'deploy_script' not in colunas_nodes:
        conn.execute("ALTER TABLE nodes ADD COLUMN deploy_script TEXT DEFAULT 'create_vps.sh'")
    
    cursor.execute("SELECT COUNT(*) FROM nodes")
    if cursor.fetchone()[0] == 0:
        agent_url = os.getenv("AGENT_URL", "https://server-1.rotava.com")
        agent_token = os.getenv("API_TOKEN", "mudar123")
        conn.execute("""INSERT INTO nodes 
            (nome, url_agente, token_agente, arquitetura, preco_usd, preco_renew_usd, preco_ano_usd, limite_vps, ram_mb, swap_mb, disk_mb, cpu_fraction, descricao_hardware, cpu_core, ordem, ipv4_base, ipv4_cidr, ipv4_gw, ipv6_base, ipv6_cidr, ipv6_gw, deploy_script) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("BananaPi (Home)", agent_url, agent_token, "armhf", 0.10, 0.10, 1.00, 10, 64, 32, 1024, "20%", "Allwinner A20 DDR2", "0", 0, "10.0.0.", "32", "10.0.0.1", "2804:14d:7e89:41a0::", "64", "fe80::1", "create_vps.sh"))
    
    conn.commit()
    conn.close()

init_db()

def get_node_info(id_vps):
    conn = sqlite3.connect("db.sqlite")
    res = conn.execute("""
        SELECT n.url_agente, n.token_agente, n.ram_mb, n.swap_mb, n.disk_mb, n.cpu_fraction, n.cpu_core, n.id, n.deploy_script
        FROM vps v JOIN nodes n ON v.node_id = n.id 
        WHERE v.id = ?
    """, (id_vps,)).fetchone()
    conn.close()
    return res if res else (None, None, 64, 32, 1024, "20%", "0", 1, "create_vps.sh")

def alocar_ips_disponiveis(node_id):
    conn = sqlite3.connect("db.sqlite")
    node = conn.execute("""
        SELECT ipv4_base, ipv4_cidr, ipv6_base, ipv6_cidr, ipv4_gw, ipv6_gw, limite_vps 
        FROM nodes WHERE id = ?
    """, (node_id,)).fetchone()
    
    if not node:
        conn.close()
        return None
        
    ipv4_base, ipv4_cidr, ipv6_base, ipv6_cidr, ipv4_gw, ipv6_gw, limite = node
    ips_em_uso = conn.execute("SELECT ipv4, ipv6 FROM vps WHERE node_id = ? AND status NOT IN ('DELETED', 'TERMINATED')", (node_id,)).fetchall()
    conn.close()
    
    v4_usados = []
    v6_usados = []
    
    for ip_v4, ip_v6 in ips_em_uso:
        if ip_v4:
            try:
                ip_sem_cidr = ip_v4.split('/')[0]
                v4_usados.append(int(ip_sem_cidr.split('.')[-1]))
            except: pass
        if ip_v6:
            try:
                ip_sem_cidr = ip_v6.split('/')[0]
                v6_usados.append(int(ipaddress.IPv6Address(ip_sem_cidr)))
            except: pass

    # CAÇANDO O BURACO NO IPv4 (Iniciando no 2)
    novo_v4_sufixo = None
    for i in range(2, limite + 5):
        if i not in v4_usados:
            novo_v4_sufixo = i
            break
            
    # CAÇANDO O BURACO NO IPv6
    base_v6_int = int(ipaddress.IPv6Address(f"{ipv6_base}0"))
    novo_v6_str = None
    for i in range(2, limite + 5):
        candidato_int = base_v6_int + i
        if candidato_int not in v6_usados:
            novo_v6_str = str(ipaddress.IPv6Address(candidato_int))
            break

    return {
        "ipv4": f"{ipv4_base}{novo_v4_sufixo}/{ipv4_cidr}" if ipv4_base else "",
        "ipv4_gw": ipv4_gw,
        "ipv6": f"{novo_v6_str}/{ipv6_cidr}" if ipv6_base else "",
        "ipv6_gw": ipv6_gw
    }

@app.get("/", response_class=HTMLResponse)
async def home(request: Request): return templates.TemplateResponse("index.html", {"request": request})

@app.get("/hosting", response_class=HTMLResponse)
async def hosting_page(request: Request): return templates.TemplateResponse("hosting.html", {"request": request})

@app.get("/vps", response_class=HTMLResponse)
async def vps_page(request: Request):
    conn = sqlite3.connect("db.sqlite")
    nodes_db = conn.execute("SELECT nome, arquitetura, preco_usd, ram_mb, swap_mb, disk_mb, cpu_fraction, descricao_hardware FROM nodes WHERE ativo = 1 ORDER BY ordem ASC, id ASC").fetchall()
    nodes = []
    for n in nodes_db:
        nodes.append({
            "nome": n[0], "arquitetura": n[1], "preco_usd": n[2], 
            "preco_pol": f"{calcular_pol_necessario(n[2]):.6f}", 
            "ram_mb": n[3], "swap_mb": n[4], "disk_mb": n[5], 
            "cpu_fraction": n[6], "descricao_hardware": n[7]
        })
    conn.close()
    return templates.TemplateResponse("vps.html", {"request": request, "nodes": nodes})

@app.get("/tos", response_class=HTMLResponse)
async def tos_page(request: Request): return templates.TemplateResponse("tos.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse("login.html", {"request": request, "msg": msg, "mode": "login"})

@app.post("/registar")
async def registar(bg_tasks: BackgroundTasks, email: str = Form(...), pw: str = Form(...), tos: str = Form(None)):
    if not tos: return RedirectResponse(url="/login?msg=You+must+accept+the+ToS", status_code=303)
    token = str(uuid.uuid4())
    conn = sqlite3.connect("db.sqlite")
    try:
        conn.execute("INSERT INTO users (email, pw, conf, token) VALUES (?, ?, ?, ?)", (email, pw, False, token))
        conn.commit()
        bg_tasks.add_task(enviar_email_confirmacao, email, f"{BASE_URL}/confirmar/{token}")
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

@app.get("/forgot", response_class=HTMLResponse)
async def forgot_page(request: Request):
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse("login.html", {"request": request, "msg": msg, "mode": "forgot"})

@app.post("/forgot")
async def forgot_post(bg_tasks: BackgroundTasks, email: str = Form(...)):
    token = str(uuid.uuid4())
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT email FROM users WHERE email=?", (email,)).fetchone()
    if user:
        conn.execute("UPDATE users SET reset_token = ? WHERE email = ?", (token, email))
        conn.commit()
        bg_tasks.add_task(enviar_email_recuperacao, email, f"{BASE_URL}/reset/{token}")
    conn.close()
    return RedirectResponse(url="/forgot?msg=If+email+exists,+recovery+sent.", status_code=303)

@app.get("/reset/{token}", response_class=HTMLResponse)
async def reset_page(request: Request, token: str):
    return templates.TemplateResponse("login.html", {"request": request, "token": token, "mode": "reset"})

@app.post("/reset/{token}")
async def reset_post(token: str, pw: str = Form(...)):
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT email FROM users WHERE reset_token=?", (token,)).fetchone()
    if user:
        conn.execute("UPDATE users SET pw = ?, reset_token = NULL WHERE reset_token = ?", (pw, token))
        conn.commit()
        conn.close()
        return RedirectResponse(url="/login?msg=Password+Updated!+Please+Login.", status_code=303)
    conn.close()
    return RedirectResponse(url="/login?msg=Invalid+or+Expired+Token", status_code=303)

@app.get("/dash", response_class=HTMLResponse)
async def dash(request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    
    conn = sqlite3.connect("db.sqlite")
    pedidos_db = conn.execute("""
        SELECT v.id, v.carteira, v.status, n.preco_usd, v.validade, n.preco_renew_usd, n.preco_ano_usd, n.nome, n.arquitetura
        FROM vps v JOIN nodes n ON v.node_id = n.id 
        WHERE v.email=? AND v.status NOT IN ('DELETED', 'TERMINATED')
    """, (email,)).fetchall()
    
    pedidos = []
    for p in pedidos_db:
        pedidos.append({
            "id": p[0], "carteira": p[1], "status": p[2], 
            "preco_usd": p[3], 
            "preco_pol": f"{calcular_pol_necessario(p[3]):.6f}", 
            "validade": p[4],
            "renew_usd": p[5], 
            "renew_pol": f"{calcular_pol_necessario(p[5]):.6f}",
            "ano_usd": p[6], 
            "ano_pol": f"{calcular_pol_necessario(p[6]):.6f}",
            "node_nome": p[7], "arquitetura": p[8]
        })
    
    nodes_db = conn.execute("SELECT id, nome, arquitetura, preco_usd, ram_mb, swap_mb, disk_mb, cpu_fraction, limite_vps, descricao_hardware, cpu_core FROM nodes WHERE ativo = 1 ORDER BY ordem ASC, id ASC").fetchall()
    nodes_disponiveis = []
    for n in nodes_db:
        uso = conn.execute("SELECT COUNT(*) FROM vps WHERE node_id=? AND status NOT IN ('DELETED', 'TERMINATED')", (n[0],)).fetchone()[0]
        nodes_disponiveis.append({
            "id": n[0], "nome": n[1], "arquitetura": n[2], "preco_usd": n[3], 
            "preco_pol": f"{calcular_pol_necessario(n[3]):.6f}", 
            "ram_mb": n[4], "swap_mb": n[5], "disk_mb": n[6], "cpu_fraction": n[7], "uso": uso, "limite": n[8], "sold_out": uso >= n[8],
            "descricao_hardware": n[9], "cpu_core": n[10]
        })
        
    tickets_db = conn.execute("SELECT id, assunto, mensagem, resposta, status, data_criacao FROM tickets WHERE email=? ORDER BY id DESC", (email,)).fetchall()
    tickets = [{"id": t[0], "assunto": t[1], "mensagem": t[2], "resposta": t[3], "status": t[4], "data": t[5]} for t in tickets_db]
        
    conn.close()
    return templates.TemplateResponse("dash.html", {"request": request, "pedidos": pedidos, "nodes": nodes_disponiveis, "tickets": tickets})

@app.post("/comprar")
async def comprar(request: Request, bg_tasks: BackgroundTasks, node_id: int = Form(...)):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    
    conn = sqlite3.connect("db.sqlite")
    node = conn.execute("SELECT preco_usd, limite_vps FROM nodes WHERE id=? AND ativo=1", (node_id,)).fetchone()
    if not node:
        conn.close()
        return RedirectResponse(url="/dash?msg=Servidor+indisponivel", status_code=303)
        
    uso = conn.execute("SELECT COUNT(*) FROM vps WHERE node_id=? AND status NOT IN ('DELETED', 'TERMINATED')", (node_id,)).fetchone()[0]
    if uso >= node[1]:
        conn.close()
        return RedirectResponse(url="/dash?msg=OUT+OF+STOCK.+No+resources+available.", status_code=303)
    
    id_pedido = "vps-" + str(uuid.uuid4())[:15]
    endereco, chave_privada = gerar_carteira()
    p_pol = float(f"{calcular_pol_necessario(node[0]):.6f}")
    
    conn.execute("INSERT INTO vps (id, email, carteira, chave_privada, status, preco_pol, node_id) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                 (id_pedido, email, endereco, chave_privada, "PENDING PAYMENT", p_pol, node_id))
    conn.commit()
    conn.close()
    
    return RedirectResponse(url="/dash", status_code=303)

@app.post("/verificar_pagamento/{id_vps}")
async def verificar_pagamento(id_vps: str, request: Request, bg_tasks: BackgroundTasks):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    
    conn = sqlite3.connect("db.sqlite")
    vps = conn.execute("""
        SELECT v.carteira, v.chave_privada, v.status, v.validade, n.preco_usd, n.preco_renew_usd, n.preco_ano_usd
        FROM vps v JOIN nodes n ON v.node_id = n.id 
        WHERE v.id=? AND v.email=?
    """, (id_vps, email)).fetchone()
    
    if not vps or vps[2] not in ['PENDING PAYMENT', 'ATIVA', 'SUSPENDED']:
        conn.close()
        return RedirectResponse(url="/dash", status_code=303)
    
    endereco, chave_privada, status, validade, preco_usd, preco_renew_usd, preco_ano_usd = vps
    
    preco_pol_base = float(f"{calcular_pol_necessario(preco_usd):.6f}")
    preco_pol_renew = float(f"{calcular_pol_necessario(preco_renew_usd):.6f}")
    preco_pol_ano = float(f"{calcular_pol_necessario(preco_ano_usd):.6f}")
    
    pago_ano = verificar_pagamento_pol(endereco, preco_pol_ano)
    pago_renew = verificar_pagamento_pol(endereco, preco_pol_renew)
    pago_base = verificar_pagamento_pol(endereco, preco_pol_base)

    sucesso_pagamento = False
    dias_add = 0

    if status == 'PENDING PAYMENT':
        if pago_base:
            sucesso_pagamento = True
            dias_add = 30
    else:
        if pago_ano:
            sucesso_pagamento = True
            dias_add = 365
        elif pago_renew:
            sucesso_pagamento = True
            dias_add = 30
    
    if sucesso_pagamento:
        if status in ['ATIVA', 'SUSPENDED'] and validade:
            conn.execute(f"""
                UPDATE vps SET status = 'ATIVA', 
                validade = CASE 
                    WHEN datetime(validade) > datetime('now') THEN datetime(validade, '+{dias_add} days')
                    ELSE datetime('now', '+{dias_add} days')
                END
                WHERE id = ?
            """, (id_vps,))
        else:
            conn.execute(f"UPDATE vps SET status = 'ATIVA', validade = datetime('now', '+{dias_add} days') WHERE id = ?", (id_vps,))
            
        conn.commit()
        conn.close()
        
        registrar_log("PAGO", f"VPS {id_vps} confirmada/renovada ({dias_add} dias).", "SUCCESS", email)
        if MINHA_CARTEIRA_PRINCIPAL:
            varrer_carteira(endereco, chave_privada, MINHA_CARTEIRA_PRINCIPAL)
            
        if status == 'PENDING PAYMENT':
            bg_tasks.add_task(processar_ativacao_apos_pagamento, id_vps, email)
        elif status == 'SUSPENDED':
            agent_url, agent_token, _, _, _, _, _, _, _ = get_node_info(id_vps)
            bg_tasks.add_task(controlar_vps, id_vps, "start", agent_url, agent_token)
            
        return RedirectResponse(url=f"/dash?msg=PAYMENT+CONFIRMED!+Added+{dias_add}+days.", status_code=303)
    else:
        conn.close()
        return RedirectResponse(url="/dash?msg=NO+FUNDS+FOUND.+Blockchain+may+take+a+minute.+Try+again.", status_code=303)

async def processar_ativacao_apos_pagamento(id_vps, email):
    agent_url, agent_token, ram, swap, disk, cpu, cpu_core, node_id, deploy_script = get_node_info(id_vps)
    if not agent_url: return
    
    rede = alocar_ips_disponiveis(node_id)
    if not rede: return
    
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE vps SET ipv4 = ?, ipv6 = ? WHERE id = ?", (rede["ipv4"], rede["ipv6"], id_vps))
    conn.commit()
    conn.close()

    resultado = await chamar_agent_banana_pi(
        id_vps, agent_url, agent_token, 
        ram_mb=ram, swap_mb=swap, disk_mb=disk, cpu_fraction=cpu, cpu_core=cpu_core,
        ipv4=rede["ipv4"], ipv4_gw=rede["ipv4_gw"],
        ipv6=rede["ipv6"], ipv6_gw=rede["ipv6_gw"],
        deploy_script=deploy_script
    )
    
    if resultado.get("sucesso"):
        registrar_log("DEPLOY_OK", f"IP: {rede['ipv6']}", "SUCCESS", email)
        enviar_email_deploy(email, id_vps, rede["ipv6"], resultado.get("senha"))

@app.post("/apagar_vps/{id_vps}")
async def apagar_vps(id_vps: str, request: Request, bg_tasks: BackgroundTasks):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    
    agent_url, agent_token, _, _, _, _, _, _, _ = get_node_info(id_vps)
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE vps SET status = 'DELETED', ipv4 = NULL, ipv6 = NULL WHERE id=? AND email=?", (id_vps, email))
    conn.commit()
    conn.close()
    
    if agent_url: bg_tasks.add_task(controlar_vps, id_vps, "delete", agent_url, agent_token)
    registrar_log("SOFT_DELETE", f"User destruiu a {id_vps}", "INFO", email)
    return RedirectResponse(url="/dash", status_code=303)

@app.post("/control_vps/{id_vps}/{acao}")
async def painel_controle_vps(id_vps: str, acao: str, request: Request, bg_tasks: BackgroundTasks):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    conn = sqlite3.connect("db.sqlite")
    vps = conn.execute("SELECT id FROM vps WHERE id=? AND email=? AND status='ATIVA'", (id_vps, email)).fetchone()
    conn.close()
    
    if vps and acao in ["start", "stop", "restart"]:
        agent_url, agent_token, _, _, _, _, _, _, _ = get_node_info(id_vps)
        if agent_url:
            bg_tasks.add_task(controlar_vps, id_vps, acao, agent_url, agent_token)
            registrar_log("LXC_POWER", f"Ordem {acao} enviada para {id_vps}", "INFO", email)
    return RedirectResponse(url="/dash", status_code=303)

@app.get("/api/status/{id_vps}")
async def get_vps_status(id_vps: str, request: Request):
    email = request.cookies.get("sessao")
    if not email: return {"error": "unauthorized"}
    agent_url, agent_token, _, _, _, _, _, _, _ = get_node_info(id_vps)
    if not agent_url: return {"error": "not found"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{agent_url}/status/{id_vps}", headers={"X-API-Key": agent_token})
            return resp.json()
    except Exception:
        return {"error": "agent unreachable"}

@app.get("/console/{id_vps}", response_class=HTMLResponse)
async def web_console(id_vps: str, request: Request):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    conn = sqlite3.connect("db.sqlite")
    vps = conn.execute("SELECT id FROM vps WHERE id=? AND email=? AND status='ATIVA'", (id_vps, email)).fetchone()
    conn.close()
    if not vps: return RedirectResponse("/dash")
    agent_url, agent_token, _, _, _, _, _, _, _ = get_node_info(id_vps)
    return templates.TemplateResponse("console.html", {"request": request, "vps_id": id_vps, "agent_url": agent_url, "token": agent_token})

@app.post("/ticket")
async def novo_ticket(request: Request, assunto: str = Form(...), mensagem: str = Form(...)):
    email = request.cookies.get("sessao")
    if not email: return RedirectResponse("/login")
    conn = sqlite3.connect("db.sqlite")
    conn.execute("INSERT INTO tickets (email, assunto, mensagem) VALUES (?, ?, ?)", (email, assunto, mensagem))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/dash?msg=Ticket+Created", status_code=303)

@app.post("/ops/ticket_reply/{ticket_id}")
async def responder_ticket(ticket_id: int, resposta: str = Form(...), admin: str = Depends(verificar_admin)):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE tickets SET resposta = ?, status = 'CLOSED' WHERE id = ?", (resposta, ticket_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/ops?msg=Ticket+Replied", status_code=303)

@app.get("/ops", response_class=HTMLResponse)
async def painel_ops(request: Request, admin: str = Depends(verificar_admin)):
    msg = request.query_params.get("msg", "")
    conn = sqlite3.connect("db.sqlite")
    usuarios = conn.execute("SELECT email, conf FROM users").fetchall()
    
    vps_geral = conn.execute("""
        SELECT v.id, v.email, v.status, v.node_id, v.validade, n.nome, n.arquitetura 
        FROM vps v JOIN nodes n ON v.node_id = n.id
    """).fetchall()
    
    nodes_db = conn.execute("SELECT id, nome, url_agente, arquitetura, preco_usd, ativo, ram_mb, swap_mb, disk_mb, cpu_fraction, limite_vps, preco_renew_usd, preco_ano_usd, token_agente, descricao_hardware, cpu_core, ordem, ipv4_base, ipv4_cidr, ipv4_gw, ipv6_base, ipv6_cidr, ipv6_gw, deploy_script FROM nodes ORDER BY ordem ASC, id ASC").fetchall()
    
    nodes = []
    for n in nodes_db:
        uso = conn.execute("SELECT COUNT(*) FROM vps WHERE node_id=? AND status NOT IN ('DELETED', 'TERMINATED')", (n[0],)).fetchone()[0]
        nodes.append(n + (uso,))
        
    tickets_db = conn.execute("SELECT id, email, assunto, mensagem, status, data_criacao, resposta FROM tickets ORDER BY CASE WHEN status='OPEN' THEN 1 ELSE 2 END, id DESC").fetchall()
        
    conn_log = sqlite3.connect("logs.sqlite")
    logs = conn_log.execute("SELECT timestamp, nivel, evento, detalhes, email FROM system_logs ORDER BY id DESC LIMIT 30").fetchall()
    conn_log.close()

    try: gas = f"{w3.from_wei(w3.eth.gas_price, 'gwei'):.1f} Gwei"
    except: gas = "OFFLINE"

    return templates.TemplateResponse("ops.html", {
        "request": request, "usuarios": usuarios, "vps": vps_geral, "logs": logs, "nodes": nodes, "gas": gas, "tickets": tickets_db, "msg": msg
    })

@app.post("/ops/add_node")
async def ops_add_node(
    request: Request, nome: str = Form(...), url_agente: str = Form(...), token_agente: str = Form(...), 
    arquitetura: str = Form(...), preco_usd: float = Form(...), preco_renew_usd: float = Form(...), preco_ano_usd: float = Form(...), limite: int = Form(...),
    ram_mb: int = Form(...), swap_mb: int = Form(...), disk_mb: int = Form(...), cpu_fraction: str = Form(...),
    descricao_hardware: str = Form(...), cpu_core: str = Form(...), ordem: int = Form(0),
    ipv4_base: str = Form(...), ipv4_cidr: str = Form(...), ipv4_gw: str = Form(...),
    ipv6_base: str = Form(...), ipv6_cidr: str = Form(...), ipv6_gw: str = Form(...),
    deploy_script: str = Form("create_vps.sh"),
    admin: str = Depends(verificar_admin)
):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("""
        INSERT INTO nodes (nome, url_agente, token_agente, arquitetura, preco_usd, preco_renew_usd, preco_ano_usd, limite_vps, ram_mb, swap_mb, disk_mb, cpu_fraction, descricao_hardware, cpu_core, ordem, ipv4_base, ipv4_cidr, ipv4_gw, ipv6_base, ipv6_cidr, ipv6_gw, deploy_script) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (nome, url_agente, token_agente, arquitetura, preco_usd, preco_renew_usd, preco_ano_usd, limite, ram_mb, swap_mb, disk_mb, cpu_fraction, descricao_hardware, cpu_core, ordem, ipv4_base, ipv4_cidr, ipv4_gw, ipv6_base, ipv6_cidr, ipv6_gw, deploy_script))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/ops?msg=Node+Adicionado+com+Sucesso", status_code=303)

@app.post("/ops/edit_node/{node_id}")
async def ops_edit_node(
    node_id: int, request: Request, nome: str = Form(...), url_agente: str = Form(...), token_agente: str = Form(...), 
    arquitetura: str = Form(...), preco_usd: float = Form(...), preco_renew_usd: float = Form(...), preco_ano_usd: float = Form(...), limite: int = Form(...),
    ram_mb: int = Form(...), swap_mb: int = Form(...), disk_mb: int = Form(...), cpu_fraction: str = Form(...),
    descricao_hardware: str = Form(...), cpu_core: str = Form(...), ordem: int = Form(0),
    ipv4_base: str = Form(...), ipv4_cidr: str = Form(...), ipv4_gw: str = Form(...),
    ipv6_base: str = Form(...), ipv6_cidr: str = Form(...), ipv6_gw: str = Form(...),
    deploy_script: str = Form("create_vps.sh"),
    admin: str = Depends(verificar_admin)
):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("""
        UPDATE nodes SET 
            nome=?, url_agente=?, token_agente=?, arquitetura=?, preco_usd=?, preco_renew_usd=?, preco_ano_usd=?, 
            limite_vps=?, ram_mb=?, swap_mb=?, disk_mb=?, cpu_fraction=?, descricao_hardware=?, cpu_core=?, ordem=?,
            ipv4_base=?, ipv4_cidr=?, ipv4_gw=?, ipv6_base=?, ipv6_cidr=?, ipv6_gw=?, deploy_script=?
        WHERE id=?
    """, (nome, url_agente, token_agente, arquitetura, preco_usd, preco_renew_usd, preco_ano_usd, limite, ram_mb, swap_mb, disk_mb, cpu_fraction, descricao_hardware, cpu_core, ordem, ipv4_base, ipv4_cidr, ipv4_gw, ipv6_base, ipv6_cidr, ipv6_gw, deploy_script, node_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/ops?msg=Node+Atualizado+com+Sucesso", status_code=303)

@app.post("/ops/force_activate/{id_vps}")
async def force_activate(id_vps: str, bg_tasks: BackgroundTasks, admin: str = Depends(verificar_admin)):
    conn = sqlite3.connect("db.sqlite")
    user = conn.execute("SELECT email FROM vps WHERE id = ?", (id_vps,)).fetchone()
    if user:
        conn.execute("UPDATE vps SET status = 'MANUAL_START', validade = datetime('now', '+30 days') WHERE id = ?", (id_vps,))
        conn.commit()
        bg_tasks.add_task(processar_ativacao_manual, id_vps, user[0])
    conn.close()
    return RedirectResponse(url="/ops?msg=Manual+Deploy+Started", status_code=303)

async def processar_ativacao_manual(id_vps, email):
    agent_url, agent_token, ram, swap, disk, cpu, cpu_core, node_id, deploy_script = get_node_info(id_vps)
    if not agent_url: return
    
    rede = alocar_ips_disponiveis(node_id)
    if not rede: return
    
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE vps SET ipv4 = ?, ipv6 = ? WHERE id = ?", (rede["ipv4"], rede["ipv6"], id_vps))
    conn.commit()
    conn.close()

    res = await chamar_agent_banana_pi(
        id_vps, agent_url, agent_token, 
        ram_mb=ram, swap_mb=swap, disk_mb=disk, cpu_fraction=cpu, cpu_core=cpu_core,
        ipv4=rede["ipv4"], ipv4_gw=rede["ipv4_gw"],
        ipv6=rede["ipv6"], ipv6_gw=rede["ipv6_gw"],
        deploy_script=deploy_script
    )
    
    if res.get("sucesso"):
        conn = sqlite3.connect("db.sqlite")
        conn.execute("UPDATE vps SET status = 'ATIVA' WHERE id = ?", (id_vps,))
        conn.commit()
        conn.close()
        enviar_email_deploy(email, id_vps, rede["ipv6"], res.get("senha"))

@app.post("/ops/nuke/{id_vps}")
async def nuke_vps(id_vps: str, admin: str = Depends(verificar_admin)):
    conn = sqlite3.connect("db.sqlite")
    conn.execute("UPDATE vps SET status = 'BANNED', ipv4 = NULL, ipv6 = NULL WHERE id = ?", (id_vps,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/ops?msg=Nuked", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)