from flask import Flask, request, jsonify
import subprocess
import os
from dotenv import load_dotenv
import secrets
import string

load_dotenv()

app = Flask(__name__)
API_KEY = os.getenv("API_KEY", "mudar123")

def check_auth():
    token = request.headers.get("X-API-Key")
    return token == API_KEY

def run_cmd(cmd):
    try:
        resultado = subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return True, resultado.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def gerar_senha_segura(tamanho=12):
    caracteres = string.ascii_letters + string.digits
    return ''.join(secrets.choice(caracteres) for _ in range(tamanho))

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
    
    # 1. Pega o nome do script e HIGIENIZA (Evita Path Traversal)
    script_cru = data.get('deploy_script', 'create_vps.sh')
    script_seguro = os.path.basename(script_cru) # Remove barras e caminhos (ex: ../../script.sh vira script.sh)
    
    if not script_seguro.endswith('.sh'):
        script_seguro += '.sh'
        
    if not os.path.exists(script_seguro):
        print(f"❌ [ERRO] O script {script_seguro} não existe neste node!")
        return jsonify({"error": f"Script {script_seguro} not found on node"}), 400

    try:
        cpu_percent = int(str(cpu_fraction).replace('%', '').strip())
        cpu_quota = int((cpu_percent / 100.0) * 100000)
    except:
        cpu_quota = 20000 
    
    senha = gerar_senha_segura()

    print(f"\n🚀 [DEPLOY INICIADO] {vps_id} usando {script_seguro}")

    # 2. Chama o script dinâmico de forma segura
    cmd = f"./{script_seguro} '{vps_id}' '{distro}' '{ram}' '{swap}' '{disk}' '{cpu_quota}' '{cpu_core}' '{ipv4}' '{ipv4_gw}' '{ipv6}' '{ipv6_gw}' '{senha}'"
    
    sucesso, output = run_cmd(cmd)
    
    if not sucesso:
        print(f"❌ [ERRO DEPLOY] {output}")
        return jsonify({"error": "Deploy script failed", "details": output}), 500

    print(f"✅ [SUCESSO] {vps_id} finalizada pelo {script_seguro}!")
    return jsonify({"status": "success", "vps": vps_id, "pass": senha})

@app.route('/control/<acao>', methods=['POST'])
def control_vps(acao):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    vps_id = request.json.get('vps_id')
    comandos = {"start": f"lxc-start -n {vps_id}", "stop": f"lxc-stop -n {vps_id}", "restart": f"lxc-stop -n {vps_id} && lxc-start -n {vps_id}", "delete": f"lxc-stop -n {vps_id} ; lxc-destroy -n {vps_id}"}
    if acao not in comandos: return jsonify({"error": "Invalid action"}), 400
    sucesso, output = run_cmd(comandos[acao])
    return jsonify({"status": "success"}) if sucesso else (jsonify({"error": "Command failed", "details": output}), 500)

@app.route('/status/<vps_id>', methods=['GET'])
def status_vps(vps_id):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401
    sucesso, output = run_cmd(f"lxc-info -n {vps_id}")
    if sucesso:
        state = "RUNNING" if "State:          RUNNING" in output else "STOPPED"
        return jsonify({"vps_id": vps_id, "status": state, "raw": output})
    return jsonify({"error": "Failed"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)