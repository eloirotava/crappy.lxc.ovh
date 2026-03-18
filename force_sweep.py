import sqlite3
from web3 import Web3

# --- CONFIGURAÇÕES ---
DB_NAME = "db.sqlite"
CARTEIRA_DESTINO = "0xf352e1A6406FDc1577051e65782594eDde2d52Ce"
VPS_ID = "vps-8891a3b2" # O ID que você me passou

# Lista de RPCs para não dar erro de conexão
RPC_URLS = [
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://polygon-mainnet.public.blastapi.io",
    "https://rpc.ankr.com/polygon"
]

def buscar_chave_no_banco(vps_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT chave_privada FROM vps WHERE id = ?", (vps_id,))
        resultado = cursor.fetchone()
        conn.close()
        if resultado:
            chave = resultado[0]
            # Garante que tenha o 0x na frente
            if not chave.startswith("0x"):
                chave = "0x" + chave
            return chave
        return None
    except Exception as e:
        print(f"[-] Erro ao ler banco: {e}")
        return None

def conectar_polygon():
    for url in RPC_URLS:
        print(f"[*] Tentando conexão: {url}")
        w3 = Web3(Web3.HTTPProvider(url))
        if w3.is_connected():
            print("[+] Conectado!")
            return w3
    return None

def executar_resgate():
    # 1. Busca a chave real (sem cortes do terminal)
    chave_privada = buscar_chave_no_banco(VPS_ID)
    if not chave_privada or len(chave_privada) < 60:
        print("[-] Erro: Chave não encontrada ou inválida no banco.")
        return

    # 2. Conecta na rede
    w3 = conectar_polygon()
    if not w3:
        print("[-] Erro: Não foi possível conectar a nenhum nó da Polygon.")
        return

    # 3. Prepara as contas
    conta = w3.eth.account.from_key(chave_privada)
    origem = conta.address
    print(f"[*] Operando na carteira: {origem}")

    # 4. Checa saldo
    saldo = w3.eth.get_balance(origem)
    if saldo == 0:
        print("[-] Saldo zerado. Talvez a transação anterior já tenha caído?")
        return
    
    print(f"[*] Saldo: {w3.from_wei(saldo, 'ether')} POL")

    # 5. Configura GAS TURBO (2.5x a média para garantir)
    gas_price = int(w3.eth.gas_price * 1.2)
    gas_limit = 21000
    custo_gas = gas_limit * gas_price
    valor_final = saldo - custo_gas

    if valor_final <= 0:
        print("[-] Saldo insuficiente para pagar o gás turbo.")
        return

    # 6. Monta a transação (usando o Nonce atual para "atropelar" a pendente)
    tx = {
        'nonce': w3.eth.get_transaction_count(origem),
        'to': CARTEIRA_DESTINO,
        'value': valor_final,
        'gas': gas_limit,
        'gasPrice': gas_price,
        'chainId': 137
    }

    # 7. Assina e envia
    try:
        print("[*] Enviando transação de resgate...")
        signed_tx = w3.eth.account.sign_transaction(tx, chave_privada)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"\n[🚀] SUCESSO ABSOLUTO!")
        print(f"[🚀] Link: https://polygonscan.com/tx/{tx_hash.hex()}")
    except Exception as e:
        print(f"[-] Falha no envio: {e}")

if __name__ == "__main__":
    executar_resgate()