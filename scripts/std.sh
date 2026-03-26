#!/bin/bash
# ==========================================
# CRAPPY CLOUD - DEPLOY ULTRA MINIMALISTA
# ==========================================

# Recebendo os 12 parâmetros do agent.py
VPS_ID=$1
DISTRO=$2
RAM_MB=$3
SWAP_MB=$4
DISK_MB=$5
CPU_QUOTA=$6
CPU_CORE=$7
IPV4=$8
IPV4_GW=$9
IPV6=${10}
IPV6_GW=${11}
ROOT_PASS=${12}

LOG_FILE="/var/log/crappy_deploy_${VPS_ID}.log"

echo "=== INICIANDO DEPLOY MINIMALISTA: $VPS_ID ===" > $LOG_FILE
date >> $LOG_FILE

# 1. Trava de Segurança
if lxc-info -n "$VPS_ID" >/dev/null 2>&1; then
    echo "[ERRO] VPS $VPS_ID ja existe!" >> $LOG_FILE
    exit 1
fi

# Remove "vps-" para encurtar o nome da veth (Evita erro de limite no kernel)
SHORT_NAME=${VPS_ID#vps-}

echo "[*] Criando Imagem Loop de ${DISK_MB}MB..." >> $LOG_FILE
# Cria a VPS usando imagem em loop de tamanho exato dinâmico
lxc-create -n "$VPS_ID" -B loop --fssize ${DISK_MB}M -f /root/conf-priv -t download -- -d "$DISTRO" -r "edge" -a "amd64" >> $LOG_FILE 2>&1

LXC_PATH="/var/lib/lxc/$VPS_ID"
ROOTFS="$LXC_PATH/rootfs"

echo "[*] Injetando Configuração e Cgroups..." >> $LOG_FILE
# 2. Injeta o Config Limitado
cat <<EOF > "$LXC_PATH/config"
lxc.include = /usr/share/lxc/config/common.conf
lxc.arch = linux64
lxc.uts.name = $VPS_ID

# Disco Imagem
lxc.rootfs.path = loop:$LXC_PATH/rootdev
lxc.apparmor.allow_incomplete = 1

# Rede Roteada L3
lxc.net.0.type = veth
lxc.net.0.flags = up
lxc.net.0.veth.pair = $SHORT_NAME
lxc.net.0.ipv4.address = $IPV4
lxc.net.0.ipv4.gateway = $IPV4_GW
lxc.net.0.ipv6.address = $IPV6
lxc.net.0.ipv6.gateway = $IPV6_GW
lxc.mount.entry = /dev/fuse dev/fuse none bind,create=file 0 0
lxc.mount.entry = /dev/net/tun dev/net/tun none bind,create=file 0 0
lxc.cgroup2.devices.allow = c 10:229 rwm
lxc.cgroup2.devices.allow = c 10:200 rwm
lxc.idmap = u 0 100000 65536
lxc.idmap = g 0 100000 65536
lxc.net.0.script.up = /root/lxc.sh
lxc.net.0.script.down = /root/lxc.sh
lxc.environment = PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Limites Brutais (cgroup2)
lxc.cgroup2.memory.max = ${RAM_MB}M
lxc.cgroup2.memory.swap.max = ${SWAP_MB}M
lxc.cgroup2.cpu.max = $CPU_QUOTA 100000
EOF

# Pinar CPU se solicitado pelo Painel
if [ "$CPU_CORE" != "0" ] && [ "$CPU_CORE" != "" ]; then
    echo "lxc.cgroup2.cpuset.cpus = $CPU_CORE" >> "$LXC_PATH/config"
fi

lxc-start -n "$VPS_ID"

sleep 2

lxc-attach -n "$VPS_ID" -- sh -c "echo -e 'nameserver 1.1.1.1\nnameserver 2606:4700:4700::1111' > /etc/resolv.conf"
#lxc-attach -n "$VPS_ID" -- sh -c "echo -e 'auto lo\niface lo inet loopback' > /etc/network/interfaces"
lxc-attach -n "$VPS_ID" -- sh -c "echo -e 'auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet manual' > /etc/network/interfaces"

lxc-attach -n "$VPS_ID" -- apk update >> $LOG_FILE 2>&1
lxc-attach -n "$VPS_ID" -- apk add dropbear procps >> $LOG_FILE 2>&1
lxc-attach -n "$VPS_ID" -- rc-update add dropbear

echo "root:$ROOT_PASS" | lxc-attach -n "$VPS_ID" -- chpasswd

lxc-attach -n "$VPS_ID" -- rm -rf /var/cache/apk/*
lxc-stop -n "$VPS_ID" >> $LOG_FILE 2>&1

echo "[*] Iniciando a VPS Minimalista..." >> $LOG_FILE
lxc-start -n "$VPS_ID" >> $LOG_FILE 2>&1




echo "[SUCESSO] VPS $VPS_ID finalizada e rodando Dropbear!" >> $LOG_FILE
exit 0

