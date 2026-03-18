import sqlite3
from datetime import datetime

LOG_DB = "logs.sqlite"

def init_log_db():
    conn = sqlite3.connect(LOG_DB)
    conn.execute('''CREATE TABLE IF NOT EXISTS system_logs 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                     timestamp DATETIME, 
                     nivel TEXT, 
                     evento TEXT, 
                     detalhes TEXT, 
                     email TEXT)''')
    conn.commit()
    conn.close()

def registrar_log(evento, detalhes="", nivel="INFO", email="SYSTEM"):
    try:
        conn = sqlite3.connect(LOG_DB)
        conn.execute("INSERT INTO system_logs (timestamp, nivel, evento, detalhes, email) VALUES (?, ?, ?, ?, ?)",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), nivel, evento, detalhes, email))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[CRITICAL] Falha ao escrever log: {e}")

init_log_db()