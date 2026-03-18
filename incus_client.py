import httpx
import os
from dotenv import load_dotenv

load_dotenv()

BUNKER_URL = os.getenv("BUNKER_URL")
API_KEY = os.getenv("BUNKER_API_KEY")

async def chamar_agent_banana_pi(id_pedido):
    url = f"{BUNKER_URL}/api/deploy?id_pedido={id_pedido}&ram=64MiB"
    headers = {"X-API-KEY": API_KEY}
    
    # Timeout longo de 45s porque o Incus demora a baixar a imagem e ligar
    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            resp = await client.post(url, headers=headers)
            return resp.json()
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}