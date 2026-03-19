import sqlite3
import asyncio
from lxc_client import controlar_vps

async def reap():
    print("[REAPER] Iniciando o expurgo de caloteiros...")
    conn = sqlite3.connect("db.sqlite")
    
    # 1. Suspende quem acabou de vencer (status ATIVA, validade < agora)
    vencidos = conn.execute("""
        SELECT v.id, n.url_agente, n.token_agente 
        FROM vps v JOIN nodes n ON v.node_id = n.id 
        WHERE v.status = 'ATIVA' AND datetime(v.validade) <= datetime('now')
    """).fetchall()
    
    for v in vencidos:
        id_vps, url, token = v
        print(f"[REAPER] 🛑 Suspendendo {id_vps} (Validade expirada)")
        await controlar_vps(id_vps, "stop", url, token)
        conn.execute("UPDATE vps SET status = 'SUSPENDED' WHERE id = ?", (id_vps,))
        
    # 2. Deleta quem foi abandonado (status SUSPENDED, validade < agora - 7 dias)
    abandonados = conn.execute("""
        SELECT v.id, n.url_agente, n.token_agente 
        FROM vps v JOIN nodes n ON v.node_id = n.id 
        WHERE v.status = 'SUSPENDED' AND datetime(v.validade) <= datetime('now', '-7 days')
    """).fetchall()
    
    for v in abandonados:
        id_vps, url, token = v
        print(f"[REAPER] 💀 Destruindo {id_vps} (Mais de 7 dias sem pagar)")
        await controlar_vps(id_vps, "delete", url, token)
        conn.execute("UPDATE vps SET status = 'TERMINATED' WHERE id = ?", (id_vps,))

    conn.commit()
    conn.close()
    print("[REAPER] O trabalho está feito.")

if __name__ == "__main__":
    asyncio.run(reap())