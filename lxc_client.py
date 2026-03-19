import os
import asyncio
import httpx

async def chamar_agent_banana_pi(id_vps, agent_url, agent_token, distro="alpine", ram_mb=64, swap_mb=32, disk_mb=1024, cpu_fraction="20%", cpu_core="0"):
    """
    Solicita ao agente remoto a criação da VPS, incluindo limite de CPU e qual Core isolar.
    """
    headers = {"X-API-Key": agent_token}
    
    payload = {
        "vps_id": id_vps,
        "distro": distro,
        "ram_mb": ram_mb,
        "swap_mb": swap_mb,
        "disk_mb": disk_mb,
        "cpu_fraction": cpu_fraction,
        "cpu_core": str(cpu_core)
    }
    
    print(f"[AGENT] Criando {id_vps} em {agent_url} | Core: {cpu_core} | CPU: {cpu_fraction} | Swap: {swap_mb}MB")
    
    async with httpx.AsyncClient(timeout=150.0) as client:
        try:
            resp = await client.post(f"{agent_url}/create", json=payload, headers=headers)
            
            if resp.status_code != 200:
                print(f"[AGENT] Erro na criação: {resp.text}")
                return {"sucesso": False}
                
            dados = resp.json()
            senha = dados.get("pass")
            
            ip_alocado = "Aguardando Rede..."
            for _ in range(15):
                await asyncio.sleep(2)
                res_status = await client.get(f"{agent_url}/status/{id_vps}", headers=headers)
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
            return {"sucesso": True, "ip": ip_alocado, "senha": senha}
            
        except Exception as e:
            print(f"[AGENT] Falha de comunicação com o servidor: {e}")
            return {"sucesso": False}

async def controlar_vps(id_vps, acao, agent_url, agent_token):
    headers = {"X-API-Key": agent_token}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{agent_url}/control/{acao}", json={"vps_id": id_vps}, headers=headers)
            return resp.status_code == 200
        except Exception as e:
            print(f"[AGENT] Falha ao enviar controle: {e}")
            return False