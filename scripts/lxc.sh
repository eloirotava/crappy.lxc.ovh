#!/bin/bash
# Hook unificado de Rede do LXC (Corrigido os Argumentos)

NAME=$1
ACTION=$3    # O LXC passa 'up' ou 'down' no 3º argumento
IFACE=$5     # O nome da interface chega no 5º argumento

# Log para ver todos os argumentos e garantir
LOG="/tmp/lxc-rotas.log"
echo "--- [$(date)] LXC: $NAME | Ação: $ACTION | Iface: $IFACE | Args Totais: $@ ---" >> "$LOG"

# Se a interface não existir, aborta
if [ -z "$IFACE" ]; then
    echo "Erro: Nenhuma interface informada." >> "$LOG"
    exit 0
fi

CONFIG_FILE="/var/lib/lxc/$NAME/config"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Erro: Arquivo $CONFIG_FILE nao encontrado!" >> "$LOG"
    exit 0
fi

# Lê os IPs do config
IPV4=$(grep "lxc.net.0.ipv4.address" "$CONFIG_FILE" | awk '{print $3}')
IPV6=$(grep "lxc.net.0.ipv6.address" "$CONFIG_FILE" | awk '{print $3}')

echo "IPs Lidos -> IPv4: $IPV4 | IPv6: $IPV6" >> "$LOG"

# Agora o $ACTION vai casar certinho com "up" ou "down"
if [ "$ACTION" == "up" ]; then
    
    if [ -n "$IPV4" ]; then 
        ip route add "$IPV4" dev "$IFACE" >> "$LOG" 2>&1
        echo "IPv4: Rota $IPV4 adicionada no $IFACE." >> "$LOG"
    fi
    
    if [ -n "$IPV6" ]; then 
        ip -6 route add "$IPV6" dev "$IFACE" >> "$LOG" 2>&1
        ip -6 addr add fe80::1/64 dev "$IFACE" >> "$LOG" 2>&1
        echo "IPv6: Rota $IPV6 adicionada no $IFACE." >> "$LOG"
    fi

elif [ "$ACTION" == "down" ]; then

    if [ -n "$IPV4" ]; then ip route del "$IPV4" dev "$IFACE" 2>/dev/null; fi
    if [ -n "$IPV6" ]; then ip -6 route del "$IPV6" dev "$IFACE" 2>/dev/null; fi
    echo "Rotas removidas." >> "$LOG"

fi

exit 0
