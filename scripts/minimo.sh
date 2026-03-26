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
lxc.idmap = u 0 100000 65536
lxc.idmap = g 0 100000 65536
lxc.net.0.script.up = /root/lxc.sh
lxc.net.0.script.down = /root/lxc.sh

# Limites Brutais (cgroup2)
lxc.cgroup2.memory.max = ${RAM_MB}M
lxc.cgroup2.memory.swap.max = ${SWAP_MB}M
lxc.cgroup2.cpu.max = $CPU_QUOTA 100000
EOF

# Pinar CPU se solicitado pelo Painel
if [ "$CPU_CORE" != "0" ] && [ "$CPU_CORE" != "" ]; then
    echo "lxc.cgroup2.cpuset.cpus = $CPU_CORE" >> "$LXC_PATH/config"
fi

# =========================================================
# 3. MÁGICA DO CHROOT EM DISCO VIRTUAL
echo "[*] Montando disco e injetando Dropbear..." >> $LOG_FILE
mount -o loop "$LXC_PATH/rootdev" "$ROOTFS"
# =========================================================

cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"

chroot "$ROOTFS" apk update >> $LOG_FILE 2>&1
chroot "$ROOTFS" apk add dropbear procps >> $LOG_FILE 2>&1

mkdir -p "$ROOTFS/etc/dropbear"
chroot "$ROOTFS" dropbearkey -t ecdsa -f /etc/dropbear/dropbear_ecdsa_host_key 2>/dev/null
chroot "$ROOTFS" dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key 2>/dev/null
chroot "$ROOTFS" dropbearkey -t rsa -f /etc/dropbear/dropbear_rsa_host_key 2>/dev/null

chown 100000:100000 "$ROOTFS/etc/dropbear/"*
chmod 600 "$ROOTFS/etc/dropbear/"*_key

# O Inittab Perfeito (Sem runlevels, direto no kernel)
cat <<EOF > "$ROOTFS/etc/inittab"
::sysinit:/sbin/ip link set lo up
::respawn:/usr/sbin/dropbear -F -E
::ctrlaltdel:/sbin/reboot
EOF

# Configura a Senha Dinâmica gerada pelo agent.py
echo "root:$ROOT_PASS" | chroot "$ROOTFS" chpasswd

echo -e "auto lo\niface lo inet loopback" > "$ROOTFS/etc/network/interfaces"
echo -e "nameserver 1.1.1.1\nnameserver 2606:4700:4700::1111" > "$ROOTFS/etc/resolv.conf"
rm -rf "$ROOTFS/var/cache/apk/*"

# =========================================================
# FIM DA MÁGICA: Desmonta o disco
umount "$ROOTFS"
# =========================================================

echo "[*] Iniciando a VPS Minimalista..." >> $LOG_FILE
lxc-start -n "$VPS_ID" >> $LOG_FILE 2>&1

echo "[SUCESSO] VPS $VPS_ID finalizada e rodando Dropbear!" >> $LOG_FILE
exit 0
