from web3 import Web3
import uuid
import requests

w3 = Web3(Web3.HTTPProvider('https://polygon-rpc.com'))

def gerar_carteira():
    conta = w3.eth.account.create(str(uuid.uuid4()))
    return conta.address, conta.key.hex()

def obter_cotacao_pol_usd():
    """Busca o preço ao vivo na Binance."""
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=POLUSDT"
        resposta = requests.get(url).json()
        return float(resposta["price"])
    except Exception as e:
        print(f"Erro na cotação: {e}")
        return 0.10 # Backup caso a API da Binance caia

def calcular_pol_necessario(valor_usd_desejado):
    preco_atual = obter_cotacao_pol_usd()
    valor_em_pol = valor_usd_desejado / preco_atual
    return round(valor_em_pol, 2)

def verificar_pagamento_pol(endereco, valor_esperado_pol):
    saldo_wei = w3.eth.get_balance(endereco)
    saldo_pol = float(w3.from_wei(saldo_wei, 'ether'))
    # Damos uma margem de 2% por conta de arredondamentos de corretoras
    return saldo_pol >= (valor_esperado_pol * 0.98)