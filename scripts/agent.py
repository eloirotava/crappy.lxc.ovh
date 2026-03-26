from gevent import monkey
monkey.patch_all()

import os
import subprocess
import secrets
import string
import pty
import fcntl
import termios
import struct
import select
import signal
import platform
import shutil
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

print(">>> [INIT] A arrancar motor WebSocket (Gevent + PTY Nativo)...")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent', logger=True, engineio_logger=True)

API_KEY = os.getenv("API_KEY", "mudar123")
pty_sessions = {}

def check_auth():
    return request.headers.get("X-API-Key") == API_KEY

def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return True, res.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def gerar_senha_segura(tamanho=12):
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(tamanho))

# ==========================================
# ROTAS HTTP (API PADRÃO)
# ==========================================

@app.route('/create', methods=['POST'])
def create_vps():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    vps_id = data.get('vps_id')
    distro = data.get('distro', 'alpine')
    ram = data.get('ram_mb', 64)
    swap = data.get('swap_mb', 32)
    disk = data.get('disk_mb', 1024)
    cpu_fraction = data.get('cpu_fraction', '20%')
    cpu_core = data.get('cpu_core', '0')
    ipv4 = data.get('ipv4', '10.0.0.99/32')
    ipv4_gw = data.get('ipv4_gw', '10.0.0.1')
    ipv6 = data.get('ipv6', '2804:14d:7e89:41a0::99/64')
    ipv6_gw = data.get('ipv6_gw', 'fe80::1')
    
    script_seguro = os.path.basename(data.get('deploy_script', 'create_vps.sh'))
    if not script_seguro.endswith('.sh'): script_seguro += '.sh'
    if not os.path.exists(script_seguro): return jsonify({"error": f"Script not found"}), 400

    try: cpu_quota = int((int(str(cpu_fraction).replace('%', '').strip()) / 100.0) * 100000)
    except: cpu_quota = 20000 
    
    senha = gerar_senha_segura()
    cmd = f"./{script_seguro} '{vps_id}' '{distro}' '{ram}' '{swap}' '{disk}' '{cpu_quota}' '{cpu_core}' '{ipv4}' '{ipv4_gw}' '{ipv6}' '{ipv6_gw}' '{senha}'"
    
    sucesso, output = run_cmd(cmd)
    if not sucesso: return jsonify({"error": "Deploy failed", "details": output}), 500
    return jsonify({"status": "success", "vps": vps_id, "pass": senha})

@app.route('/rebuild', methods=['POST'])
def rebuild_vps():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    vps_id = data.get('vps_id')
    distro = data.get('distro', 'alpine')
    release = data.get('release', 'edge')
    arch = data.get('arch', 'amd64')
    disk = data.get('disk_mb', 1024)

    cmd = f"./rebuild_vps.sh '{vps_id}' '{distro}' '{release}' '{arch}' '{disk}'"
    sucesso, output = run_cmd(cmd)
    if not sucesso: return jsonify({"error": "Rebuild failed", "details": output}), 500
    return jsonify({"status": "success"})

@app.route('/templates', methods=['GET'])
def list_templates():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    
    arch_map = {'x86_64': 'amd64', 'aarch64': 'arm64', 'armv7l': 'armhf', 'armv8l': 'arm64'}
    local_arch = arch_map.get(platform.machine(), 'amd64')

    cmd = "/usr/share/lxc/templates/lxc-download --list"
    sucesso, output = run_cmd(cmd)
    
    if not sucesso:
        return jsonify({"error": "Failed to fetch"}), 500

    distros = {}
    for line in output.split('\n'):
        parts = line.split()
        if len(parts) >= 5 and parts[0] not in ["---", "Distro"]:
            distro, release, arch, variant = parts[0], parts[1], parts[2], parts[3]
            if arch == local_arch and variant == "default":
                if distro not in distros: distros[distro] = []
                if release not in distros[distro]: distros[distro].append(release)

    return jsonify(distros)

@app.route('/control/<acao>', methods=['POST'])
def control_vps(acao):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    vps_id = request.json.get('vps_id')
    comandos = {"start": f"lxc-start -n {vps_id} -d", "stop": f"lxc-stop -n {vps_id} -k", "restart": f"lxc-stop -n {vps_id} -k && lxc-start -n {vps_id} -d", "delete": f"lxc-stop -n {vps_id} -k ; lxc-destroy -f -n {vps_id}"}
    if acao not in comandos: return jsonify({"error": "Invalid action"}), 400
    sucesso, output = run_cmd(comandos[acao])
    return jsonify({"status": "success"}) if sucesso else (jsonify({"error": "Failed", "details": output}), 500)

@app.route('/status/<vps_id>', methods=['GET'])
def status_vps(vps_id):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    sucesso, output = run_cmd(f"lxc-info -n {vps_id}")
    if sucesso:
        state = "RUNNING" if "State:          RUNNING" in output else "STOPPED"
        ipv4_list, ipv6_list = [], []
        if state == "RUNNING":
            for line in output.split('\n'):
                if line.startswith("IP:"):
                    ip = line.split("IP:")[1].strip()
                    if ":" in ip: ipv6_list.append(ip)
                    else: ipv4_list.append(ip)
        return jsonify({"vps_id": vps_id, "status": state, "ipv4": ipv4_list, "ipv6": ipv6_list})
    return jsonify({"error": "Failed"}), 500

# ==========================================
# BACKUP E RESTORE (ZSTD)
# ==========================================

@app.route('/backup/<vps_id>', methods=['GET'])
def backup_vps(vps_id):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    img_path = f"/var/lib/lxc/{vps_id}/rootdev"
    if not os.path.exists(img_path): return jsonify({"error": "VPS disk not found"}), 404
    
    # Para a VPS para garantir consistência
    run_cmd(f"lxc-stop -n {vps_id} -k")
    
    backup_path = f"/tmp/{vps_id}_backup.img.zst"
    # Comprime com zstd (nivel 3)
    sucesso, output = run_cmd(f"zstd -T0 -3 -f {img_path} -o {backup_path}")
    if not sucesso:
        return jsonify({"error": "Backup failed", "details": output}), 500
        
    return send_file(backup_path, as_attachment=True, download_name=f"{vps_id}_backup.img.zst")

@app.route('/restore/<vps_id>', methods=['POST'])
def restore_vps(vps_id):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    file = request.files.get('file')
    if not file: return jsonify({"error": "No file uploaded"}), 400
    
    img_path = f"/var/lib/lxc/{vps_id}/rootdev"
    if not os.path.exists(img_path): return jsonify({"error": "VPS disk not found"}), 404
    
    upload_path = f"/tmp/{vps_id}_upload.zst"
    file.save(upload_path)
    
    # Para a VPS
    run_cmd(f"lxc-stop -n {vps_id} -k")
    
    # Obtém o tamanho limite do disco contratado
    orig_size = os.path.getsize(img_path)
    
    # Descomprime
    tmp_img = f"/tmp/{vps_id}_restore.img"
    sucesso, output = run_cmd(f"zstd -d -f {upload_path} -o {tmp_img}")
    if not sucesso:
        os.remove(upload_path)
        return jsonify({"error": "Decompression failed", "details": output}), 500
        
    # Verifica o tamanho (Segurança principal)
    new_size = os.path.getsize(tmp_img)
    if new_size > orig_size:
        os.remove(upload_path)
        os.remove(tmp_img)
        return jsonify({"error": f"Image too large! Exceeds VPS disk limit."}), 400
        
    # Substitui o disco e limpa temporarios
    shutil.move(tmp_img, img_path)
    os.remove(upload_path)
    return jsonify({"status": "success"})


# ==========================================
# WEBSOCKETS (MOTOR DE TERMINAL VIRTUAL NATIVO)
# ==========================================

@socketio.on('connect')
def on_connect():
    print(f"\n[WS DEBUG] Novo cliente conectado! SID: {request.sid}")

@socketio.on('connect_vps')
def on_connect_vps(data):
    if data.get('token') != API_KEY:
        emit('vps_closed')
        return
        
    vps_id = data.get('vps_id')
    try: pid, fd = pty.fork()
    except Exception as e:
        emit('vps_closed')
        return

    if pid == 0:
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        cmd = ["lxc-attach", "-n", vps_id]
        try: os.execvpe(cmd[0], cmd, env)
        except Exception as e: os._exit(1)

    master_fd = fd
    child_pid = pid
    pty_sessions[request.sid] = {'fd': master_fd, 'child_pid': child_pid}
    
    try:
        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except: pass
    
    def read_pty(fd, sid):
        while True:
            socketio.sleep(0.01)
            try:
                r, w, e = select.select([fd], [], [], 0.1)
                if fd in r:
                    output = os.read(fd, 10240)
                    if not output: break
                    socketio.emit('vps_output', {'output': output.decode('utf-8', errors='replace')}, room=sid)
            except: break
        socketio.emit('vps_closed', room=sid)

    socketio.start_background_task(read_pty, master_fd, request.sid)

    def wake_up_terminal():
        socketio.sleep(0.5)
        try: os.write(master_fd, b'\n')
        except: pass

    socketio.start_background_task(wake_up_terminal)

@socketio.on('vps_input')
def on_vps_input(data):
    session = pty_sessions.get(request.sid)
    if session:
        try: os.write(session['fd'], data['input'].encode('utf-8'))
        except: pass

@socketio.on('resize')
def on_resize(data):
    session = pty_sessions.get(request.sid)
    if session:
        try:
            winsize = struct.pack("HHHH", data.get('rows', 24), data.get('cols', 80), 0, 0)
            fcntl.ioctl(session['fd'], termios.TIOCSWINSZ, winsize)
        except: pass

@socketio.on('disconnect')
def on_disconnect():
    session = pty_sessions.pop(request.sid, None)
    if session:  
        try:  
            os.close(session['fd'])
            os.kill(session['child_pid'], signal.SIGKILL)
        except: pass

if __name__ == '__main__':
    print(">>> [READY] Servidor escutando na porta 5000...")
    socketio.run(app, host='0.0.0.0', port=5000)
