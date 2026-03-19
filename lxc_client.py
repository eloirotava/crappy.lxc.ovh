import os
import asyncio
import httpx

# Configurações do Agente (Defina isso no .env do site)
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:5000")
AGENT_TOKEN = os.getenv("API_TOKEN", "mudar123")

async def chamar_agent_banana_pi(id_vps, distro="alpine", ram_mb=64, swap_mb=32, disk_mb=1024):
    """
    Solicita ao agente remoto a criação do contêiner LXC real.
    """
    headers = {"X-API-Key": AGENT_TOKEN}
    
    # É AQUI QUE A MÁGICA ACONTECE!
    # Se o swap_mb não estiver neste dicionário, o agente nunca vai saber que você pediu 32MB.
    payload = {
        "vps_id": id_vps,
        "distro": distro,
        "ram_mb": ram_mb,
        "swap_mb": swap_mb,
        "disk_mb": disk_mb
    }
    
    print(f"[AGENT] Solicitando criação da {id_vps} no servidor {AGENT_URL} com {swap_mb}MB de Swap...")
    
    async with httpx.AsyncClient(timeout=150.0) as client:
        try:
            # 1. Manda criar a VPS
            resp = await client.post(f"{AGENT_URL}/create", json=payload, headers=headers)
            
            if resp.status_code != 200:
                print(f"[AGENT] Erro na criação: {resp.text}")
                return {"sucesso": False}
                
            dados = resp.json()
            senha = dados.get("pass")
            
            # 2. O agente reinicia a VPS no final, o IP IPv6 pode demorar uns segundos.
            ip_alocado = "Aguardando Rede..."
            for _ in range(15):
                await asyncio.sleep(2)
                res_status = await client.get(f"{AGENT_URL}/status/{id_vps}", headers=headers)
                if res_status.status_code == 200:
                    status_json = res_status.json()
                    ipv6_list = status_json.get("ipv6", [])
                    ipv4_list = status_json.get("ipv4", [])
                    
                    # Prioriza IPv6 Global (ignora link-local fe80::)
                    ips_globais = [ip for ip in ipv6_list if not ip.startswith("fe80")]
                    if ips_globais:
                        ip_alocado = ips_globais[0]
                        break
                    elif ipv4_list:
                        ip_alocado = ipv4_list[0]
                        break
            
            print(f"[AGENT] VPS {id_vps} criada com sucesso! IP: {ip_alocado}")
            return {
                "sucesso": True,
                "ip": ip_alocado,
                "senha": senha
            }
            
        except Exception as e:
            print(f"[AGENT] Falha de comunicação com o servidor: {e}")
            return {"sucesso": False}

async def controlar_vps(id_vps, acao):
    """
    Ações: 'start', 'stop', 'restart', 'delete'
    """
    headers = {"X-API-Key": AGENT_TOKEN}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{AGENT_URL}/control/{acao}", json={"vps_id": id_vps}, headers=headers)
            return resp.status_code == 200
        except Exception as e:
            print(f"[AGENT] Falha ao enviar controle: {e}")
            return False