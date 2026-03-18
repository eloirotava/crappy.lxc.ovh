from web3 import Web3
import uuid
import requests

# Provedor 1RPC: Estável e não exige chave de API para volumes baixos
w3 = Web3(Web3.HTTPProvider('https://1rpc.io/matic'))

def gerar_carteira():
    # Retorna o endereço e a chave privada para podermos resgatar o POL depois
    conta = w3.eth.account.create(str(uuid.uuid4()))
    return conta.address, conta.key.hex()

def obter_cotacao_pol_usd():
    """Busca o preço ao vivo tentando múltiplas corretoras."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    # 1. Binance
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=POLUSDT"
        resposta = requests.get(url, headers=headers, timeout=3)
        return float(resposta.json()["price"])
    except Exception:
        pass

    # 2. KuCoin
    try:
        url = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=POL-USDT"
        resposta = requests.get(url, headers=headers, timeout=3)
        return float(resposta.json()["data"]["price"])
    except Exception:
        pass

    return 0.096 # Fallback caso as APIs falhem

def calcular_pol_necessario(valor_usd_desejado):
    preco_atual = obter_cotacao_pol_usd()
    if preco_atual <= 0.01:
        preco_atual = 0.096
    valor_em_pol = valor_usd_desejado / preco_atual
    return round(valor_em_pol, 2)

def verificar_pagamento_pol(endereco, valor_esperado_pol):
    try:
        saldo_wei = w3.eth.get_balance(endereco)
        saldo_pol = float(w3.from_wei(saldo_wei, 'ether'))
        print(f"[🔍] Checking Wallet {endereco}: Found {saldo_pol} POL (Need {valor_esperado_pol})")
        return saldo_pol >= (valor_esperado_pol * 0.98)
    except Exception as e:
        print(f"Erro ao verificar saldo na blockchain: {e}")
        return False

def varrer_carteira(endereco_origem, chave_privada, endereco_destino):
    """Raspa todo o saldo da carteira temporária e envia para a principal"""
    try:
        print(f"\n[🧹] Iniciando raspa da carteira {endereco_origem}...")
        saldo_wei = w3.eth.get_balance(endereco_origem)
        
        if saldo_wei == 0:
            print("[⚠️] Carteira já está vazia. Nada a raspar.")
            return False

        gas_price = w3.eth.gas_price
        gas_limit = 21000 # Custo padrão de transferência
        custo_gas = gas_limit * gas_price
        
        valor_enviar = saldo_wei - custo_gas
        
        if valor_enviar <= 0:
            print("[⚠️] Saldo insuficiente para cobrir as taxas de gás.")
            return False
            
        tx = {
            'nonce': w3.eth.get_transaction_count(endereco_origem),
            'to': endereco_destino,
            'value': valor_enviar,
            'gas': gas_limit,
            'gasPrice': gas_price,
            'chainId': 137 # 137 é o ID da rede Polygon Mainnet
        }
        
        signed_tx = w3.eth.account.sign_transaction(tx, chave_privada)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        print(f"[💰] SWEEP REALIZADO! {w3.from_wei(valor_enviar, 'ether')} POL lucrados.")
        print(f"[🔗] Hash da transferência: {tx_hash.hex()}\n")
        return tx_hash.hex()
        
    except Exception as e:
        print(f"[❌] Erro ao realizar o sweep: {e}")
        return False