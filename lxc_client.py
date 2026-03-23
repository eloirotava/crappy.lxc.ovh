import os
import asyncio
import httpx

async def chamar_agent_banana_pi(id_vps, agent_url, agent_token, distro="alpine", ram_mb=64, swap_mb=32, disk_mb=1024, cpu_fraction="20%", cpu_core="0", ipv4=None, ipv4_gw=None, ipv6=None, ipv6_gw=None, deploy_script="create_vps.sh"):
    """
    Solicita ao agente remoto a criação da VPS, enviando IPs e qual script executar.
    """
    headers = {"X-API-Key": agent_token}
    
    payload = {
        "vps_id": id_vps,
        "distro": distro,
        "ram_mb": ram_mb,
        "swap_mb": swap_mb,
        "disk_mb": disk_mb,
        "cpu_fraction": cpu_fraction,
        "cpu_core": str(cpu_core),
        "ipv4": ipv4,
        "ipv4_gw": ipv4_gw,
        "ipv6": ipv6,
        "ipv6_gw": ipv6_gw,
        "deploy_script": deploy_script
    }
    
    print(f"[AGENT] Criando {id_vps} em {agent_url} usando {deploy_script}")
    
    async with httpx.AsyncClient(timeout=150.0) as client:
        try:
            resp = await client.post(f"{agent_url}/create", json=payload, headers=headers)
            if resp.status_code != 200:
                print(f"[AGENT] Erro na criação: {resp.text}")
                return {"sucesso": False}
                
            return {"sucesso": True, "senha": resp.json().get("pass")}
        except Exception as e:
            print(f"[AGENT] Falha: {e}")
            return {"sucesso": False}

async def controlar_vps(id_vps, acao, agent_url, agent_token):
    headers = {"X-API-Key": agent_token}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{agent_url}/control/{acao}", json={"vps_id": id_vps}, headers=headers)
            return resp.status_code == 200
        except Exception as e:
            print(f"[AGENT] Falha controle: {e}")
            return False