#!/bin/bash
# ==========================================
# CRAPPY CLOUD - REBUILD OS SCRIPT
# ==========================================

VPS_ID=$1
DISTRO=$2
RELEASE=$3
ARCH=$4
DISK_MB=$5

LOG_FILE="/var/log/crappy_rebuild_${VPS_ID}.log"
echo "=== REBUILDING $VPS_ID to $DISTRO $RELEASE ===" > $LOG_FILE
date >> $LOG_FILE

LXC_PATH="/var/lib/lxc/$VPS_ID"
BACKUP_CONF="/tmp/${VPS_ID}_config.backup"

echo "[*] Parando a VPS..." >> $LOG_FILE
lxc-stop -n "$VPS_ID" -k >> $LOG_FILE 2>&1

echo "[*] Fazendo backup do config de rede e limites..." >> $LOG_FILE
cp "$LXC_PATH/config" "$BACKUP_CONF"

echo "[*] Destruindo disco antigo..." >> $LOG_FILE
lxc-destroy -n "$VPS_ID" >> $LOG_FILE 2>&1

echo "[*] Baixando novo RootFS ($DISTRO $RELEASE $ARCH)..." >> $LOG_FILE
lxc-create -n "$VPS_ID" -B loop --fssize ${DISK_MB}M -f /root/conf-priv -t download -- -d "$DISTRO" -r "$RELEASE" -a "$ARCH" >> $LOG_FILE 2>&1

echo "[*] Restaurando configurações originais..." >> $LOG_FILE
cp "$BACKUP_CONF" "$LXC_PATH/config"
rm "$BACKUP_CONF"

echo "[*] Ligando a maquina..." >> $LOG_FILE
lxc-start -n "$VPS_ID" >> $LOG_FILE 2>&1

# Tenta injetar uma senha padrao (crappy123), mas o foco principal é o Web Console

echo "[SUCESSO] Rebuild completo! O usuario precisa configurar a rede via Web Console." >> $LOG_FILE
exit 0
