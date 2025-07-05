import os
import subprocess
import zipfile
import datetime
import json
from flask import Flask, jsonify, request
from flask_cors import CORS
from screenutils import Screen, list_screens

# --- Wczytywanie konfiguracji z pliku JSON ---
def load_agent_config():
    """Wczytuje konfigurację agenta z pliku agent_config.json."""
    try:
        with open('agent_config.json', 'r', encoding='utf-8') as f:
            return json.load()
    except FileNotFoundError:
        print("BŁĄD: Plik 'agent_config.json' nie został znaleziony. Upewnij się, że istnieje w tym samym folderze co agent.py.")
        exit()
    except json.JSONDecodeError:
        print("BŁĄD: Plik 'agent_config.json' zawiera błędy składni JSON.")
        exit()

config = load_agent_config()
SERVERS_CONFIG = config.get('servers', {})
BACKUP_DIR = config.get('backup_dir', 'backups') # Domyślna wartość to 'backups'

# Upewnij się, że folder na backupy istnieje
os.makedirs(BACKUP_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app) # Zezwolenie na zapytania z panelu webowego

def get_server_screen(server_id):
    """Znajduje screen dla danego serwera."""
    try:
        return Screen(server_id)
    except Exception:
        return None

@app.route('/status/<server_id>', methods=['GET'])
def get_status(server_id):
    """Zwraca status serwera: online, gracze."""
    if server_id not in SERVERS_CONFIG:
        return jsonify({'error': 'Server not found in agent_config.json'}), 404

    screen = get_server_screen(server_id)
    is_online = screen and screen.exists

    if is_online:
        try:
            from mcstatus import JavaServer
            server_conf = SERVERS_CONFIG[server_id]
            # Zakładamy, że serwer nasłuchuje na localhost na swoim porcie query
            server_mc = JavaServer.lookup(f"127.0.0.1:{server_conf['query_port']}")
            status = server_mc.status()
            return jsonify({
                'is_online': True,
                'players_online': status.players.online,
                'max_players': status.players.max
            })
        except Exception as e:
            return jsonify({
                'is_online': True,
                'players_online': '?',
                'max_players': '?',
                'query_error': str(e)
            })
    else:
        return jsonify({'is_online': False})


@app.route('/start/<server_id>', methods=['POST'])
def start_server(server_id):
    """Uruchamia serwer w nowej sesji screen."""
    if server_id not in SERVERS_CONFIG:
        return jsonify({'error': 'Server not found in agent_config.json'}), 404
    
    screen = get_server_screen(server_id)
    if screen and screen.exists:
        return jsonify({'status': 'error', 'message': 'Serwer jest już uruchomiony.'}), 400

    server_conf = SERVERS_CONFIG[server_id]
    script_path = os.path.join(server_conf['path'], server_conf['start_script'])
    
    if not os.path.exists(script_path):
        return jsonify({'status': 'error', 'message': f'Skrypt startowy nie został znaleziony: {script_path}'}), 404

    s = Screen(server_id, True)
    s.send_commands(f'cd {server_conf["path"]} && ./{server_conf["start_script"]}')
    
    return jsonify({'status': 'success', 'message': 'Serwer został uruchomiony.'})

@app.route('/stop/<server_id>', methods=['POST'])
def stop_server(server_id):
    """Zatrzymuje serwer, wysyłając komendę 'stop'."""
    screen = get_server_screen(server_id)
    if not (screen and screen.exists):
        return jsonify({'status': 'error', 'message': 'Serwer nie jest uruchomiony.'}), 400
    
    screen.send_commands('stop')
    return jsonify({'status': 'success', 'message': 'Wysłano polecenie zatrzymania serwera.'})

@app.route('/command/<server_id>', methods=['POST'])
def send_command(server_id):
    """Wysyła komendę do konsoli serwera."""
    screen = get_server_screen(server_id)
    if not (screen and screen.exists):
        return jsonify({'status': 'error', 'message': 'Serwer nie jest uruchomiony.'}), 400
    
    data = request.get_json()
    command = data.get('command')
    if not command:
        return jsonify({'status': 'error', 'message': 'Brak komendy.'}), 400

    screen.send_commands(command)
    return jsonify({'status': 'success', 'message': 'Komenda została wysłana.'})

@app.route('/details/<server_id>', methods=['GET'])
def get_details(server_id):
    """Pobiera szczegóły serwera: logi, listę graczy itp."""
    if server_id not in SERVERS_CONFIG:
        return jsonify({'error': 'Server not found in agent_config.json'}), 404

    server_conf = SERVERS_CONFIG[server_id]
    status_data = get_status(server_id).get_json()
    
    logs = ""
    try:
        log_path = os.path.join(server_conf['path'], 'logs', 'latest.log')
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                logs = "".join(f.readlines()[-50:])
    except Exception as e:
        logs = f"Nie udało się odczytać logów: {e}"

    console_output = ""
    screen = get_server_screen(server_id)
    if screen and screen.exists:
        console_output = "\n".join(screen.read_buffer())

    response_data = {
        'is_online': status_data.get('is_online', False),
        'players_online': status_data.get('players_online', 0),
        'player_list': [], # Wymaga RCON lub pluginu, na razie puste
        'logs': logs,
        'console_output': console_output,
    }
    return jsonify(response_data)


@app.route('/backup/<server_id>', methods=['POST'])
def create_backup(server_id):
    """Tworzy archiwum .zip folderu serwera."""
    if server_id not in SERVERS_CONFIG:
        return jsonify({'error': 'Server not found in agent_config.json'}), 404
        
    server_conf = SERVERS_CONFIG[server_id]
    server_path = server_conf['path']
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f'backup_{server_id}_{timestamp}.zip'
    backup_filepath = os.path.join(BACKUP_DIR, backup_filename)

    try:
        with zipfile.ZipFile(backup_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(server_path):
                if os.path.basename(root) == os.path.basename(BACKUP_DIR):
                    dirs[:] = []
                    continue
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, server_path)
                    zipf.write(file_path, arcname)
        return jsonify({'status': 'success', 'message': 'Backup został utworzony.', 'path': backup_filepath})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Błąd podczas tworzenia backupu: {e}'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
