import asyncio
import random

async def chamar_agent_banana_pi(id_vps):
    """
    SIMULADOR DE HARDWARE: 
    Usa isso enquanto o Banana Pi estiver offline para testar o site.
    """
    print(f"[SIMULADOR] Recebi pedido para criar a {id_vps}...")
    
    # Simula o tempo que o LXC leva para nascer (3 segundos)
    await asyncio.sleep(3) 
    
    # Simula um IP e uma senha aleatória
    ip_falso = f"2001:db8:85a3::{random.randint(100, 999)}"
    senha_falsa = "crappy_root_123"

    print(f"[SIMULADOR] VPS {id_vps} criada com sucesso no 'nó virtual'!")
    
    return {
        "sucesso": True,
        "ip": ip_falso,
        "senha": senha_falsa
    }