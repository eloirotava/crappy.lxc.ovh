                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      crappi_agent.py                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   
from gevent import monkey
monkey.patch_all()

import os, pty, subprocess, select, time
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
from dotenv import load_dotenv

load_dotenv()
SECRET_TOKEN = os.getenv("API_TOKEN", "mudar123")

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

VG_NAME = "vg_clientes"
DEFAULT_ARCH = "armhf" 
fd_dict = {}

# --- AUXILIARES ---
def check_auth():
    return request.headers.get("X-API-Key") == SECRET_TOKEN

def run_cmd(cmd):
    print(f"\n[EXEC] {cmd}") # <--- DEBUG: Mostra o comando antes de rodar
    try:
        result = subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        # <--- DEBUG: Mostra exatamente o erro do LXC/LVM
        print(f"❌ [ERRO CRÍTICO no comando]: {cmd}")
        print(f"❌ [STDERR]: {e.stderr.strip()}")
        print(f"❌ [STDOUT]: {e.stdout.strip()}")
        return False, e.stderr

def wait_for_network(vps_id, max_retries=20):
    print(f"[{vps_id}] Aguardando rede...")
    for i in range(max_retries):
        success, ip_check = run_cmd(f"lxc-info -n {vps_id} -i")
        if success and "IP:" in ip_check:
            time.sleep(3)
            return True
        time.sleep(2)
    return False

# --- API (CREATE / STATUS / CONTROL) ---

@app.route('/create', methods=['POST'])
def create_vps():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    vps_id, distro = data.get('vps_id'), data.get('distro', 'alpine')
    
    # 1. Puxando o Swap do payload (com fallback para 64MB)
    ram = data.get('ram_mb', 64)
    swap = data.get('swap_mb', 64) 
    disk = data.get('disk_mb', 256)
    
    release = "edge" if distro == "alpine" else "bookworm"
    senha = vps_id.split('vps-', 1)[1] if 'vps-' in vps_id else "mudar123"

    print(f"\n🚀 [INICIANDO DEPLOY] {vps_id} | OS: {distro} | RAM: {ram}MB | SWAP: {swap}MB | DISK: {disk}MB")

    # 1. Cria o container no LVM
    sucesso, _ = run_cmd(f"lxc-create -n {vps_id} -t download -B lvm --vgname {VG_NAME} --fssize {disk}M -- -d {distro} -r {release} -a {DEFAULT_ARCH}")
    
    if not sucesso:
        print(f"🚨 [FALHA] Não foi possível criar a VPS {vps_id}.")
        return jsonify({"error": "LXC Create Failed"}), 500

    # 3. Inicia para configurar rede e SSH
    run_cmd(f"lxc-start -n {vps_id} -d")
    if not wait_for_network(vps_id): 
        print(f"🚨 [FALHA] Rede não subiu para {vps_id}.")
        return jsonify({"error": "Net Timeout"}), 500

    # 4. Bootstrap (Instala SSH e define a senha)
    bootstrap = f"apk update && apk add openssh procps coreutils dhcpcd && rc-update add sshd default && rc-update add dhcpcd default && sed -i 's/^#.*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && echo 'root:{senha}' | chpasswd"
    if distro != "alpine":
        bootstrap = f"apt update && apt install -y openssh-server iproute2 procps && systemctl enable ssh && sed -i 's/^#.*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && echo 'root:{senha}' | chpasswd"
    
    run_cmd(f"lxc-attach -n {vps_id} -- sh -c \"{bootstrap}\"")
    
    # 5. Reinicia a máquina para aplicar as configurações
    run_cmd(f"lxc-stop -n {vps_id} -r")

    # 2. Configura limites de RAM, SWAP e CPU usando cgroups v2
    with open(f"/var/lib/lxc/{vps_id}/config", "a") as f:
        f.write(f"\nlxc.cgroup2.memory.max = {ram}M\n")
        f.write(f"lxc.cgroup2.memory.swap.max = {swap}M\n")
        f.write(f"lxc.cgroup2.cpu.max = 20000 100000\n") 
    
    print(f"✅ [SUCESSO] VPS {vps_id} entregue com limites cgroups aplicados!")
    return jsonify({"status": "success", "vps": vps_id, "pass": senha})

@app.route('/status/<vps_id>', methods=['GET'])
def status_vps(vps_id):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    success, info = run_cmd(f"lxc-info -n {vps_id}")
    if not success: return jsonify({"error": "Not Found"}), 404
    _, ip_raw = run_cmd(f"lxc-info -n {vps_id} -i")
    ipv4, ipv6 = [], []
    for line in ip_raw.strip().split('\n'):
        if "IP:" in line:
            ip = line.split(":", 1)[1].strip()
            if ":" in ip: ipv6.append(ip)
            else: ipv4.append(ip)
    return jsonify({"vps": vps_id, "online": "RUNNING" in info, "ipv4": ipv4, "ipv6": ipv6})

@app.route('/control/<action>', methods=['POST'])
def control_vps(action):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    vps_id = request.json.get('vps_id')
    cmds = {
        "start": f"lxc-start -n {vps_id} -d", 
        "stop": f"lxc-stop -n {vps_id}", 
        "restart": f"lxc-stop -n {vps_id} -r", 
        "delete": f"lxc-stop -n {vps_id} -k && lxc-destroy -n {vps_id} -f"
    }
    run_cmd(cmds.get(action, "true"))
    return jsonify({"status": "action_sent"})

# --- CONSOLE WEBSOCKET ---

@socketio.on("connect_vps")
def handle_connect_vps(data):
    if data.get("token") != SECRET_TOKEN: return False
    vps_id = data.get("vps_id")
    
    master, slave = pty.openpty()
    
    def setup_tty():
        os.setsid()
        os.login_tty(slave)

    proc = subprocess.Popen(
        ["lxc-attach", "-n", vps_id, "--", "/bin/sh", "-i"],
        preexec_fn=setup_tty,
        env={"TERM": "xterm", "HOME": "/root"}
    )
    
    os.close(slave)
    fd_dict[request.sid] = master
    os.write(master, b"\n") 
    
    socketio.start_background_task(target=read_vps_loop, sid=request.sid, fd=master, proc=proc)

def read_vps_loop(sid, fd, proc):
    while sid in fd_dict:
        socketio.sleep(0.01)
        if proc.poll() is not None:
            socketio.emit("vps_output", {"output": "\r\n[Conexao encerrada pelo shell]\r\n"}, room=sid)
            break
            
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            try:
                data = os.read(fd, 4096)
                if not data: break # EOF
                socketio.emit("vps_output", {"output": data.decode(errors='replace')}, room=sid)
            except: break

    if sid in fd_dict:
        os.close(fd_dict.pop(sid))
        socketio.emit("vps_closed", room=sid)

@socketio.on("vps_input")
def handle_input(data):
    if request.sid in fd_dict:
        try: os.write(fd_dict[request.sid], data.get("input").encode())
        except: pass

@socketio.on("disconnect")
def handle_disc():
    if request.sid in fd_dict: os.close(fd_dict.pop(request.sid))

if __name__ == '__main__':
    print("🚀 Agente ON na 5000 (Com Debug Ativo)")
    socketio.run(app, host='0.0.0.0', port=5000)