import requests
import json
from flask import Flask, render_template, jsonify, request

# --- Wczytywanie konfiguracji z pliku JSON ---
def load_panel_config():
    """Wczytuje konfigurację panelu z pliku config.json."""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load()
    except FileNotFoundError:
        print("BŁĄD: Plik 'config.json' nie został znaleziony. Upewnij się, że istnieje w tym samym folderze co panel_app.py.")
        exit()
    except json.JSONDecodeError:
        print("BŁĄD: Plik 'config.json' zawiera błędy składni JSON.")
        exit()

config = load_panel_config()
SERVERS = config.get('servers', {})

app = Flask(__name__)

@app.route('/')
def index():
    """Serwuje główną stronę panelu."""
    return render_template('index.html', servers=SERVERS)

@app.route('/api/<endpoint>/<server_id>', methods=['GET', 'POST'])
def proxy_to_agent(endpoint, server_id):
    """
    Pośrednik zapytań. Przekierowuje zapytania z frontendu do odpowiedniego agenta.
    """
    if server_id not in SERVERS:
        return jsonify({'error': 'Server not found in panel config'}), 404

    agent_url = SERVERS[server_id]['machine']
    full_target_url = f"{agent_url}/{endpoint}/{server_id}"

    try:
        headers = {'Content-Type': 'application/json'}
        if request.method == 'POST':
            response = requests.post(full_target_url, json=request.get_json(), timeout=10, headers=headers)
        else: # GET
            response = requests.get(full_target_url, timeout=5)
        
        response.raise_for_status() # Rzuci wyjątkiem dla statusów 4xx/5xx
        return jsonify(response.json()), response.status_code
        
    except requests.exceptions.HTTPError as e:
        # Próba zwrócenia błędu od agenta, jeśli jest w formacie JSON
        try:
            return jsonify(e.response.json()), e.response.status_code
        except json.JSONDecodeError:
            return jsonify({'error': 'HTTP Error from agent', 'details': str(e)}), e.response.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({
            'error': 'Agent is unreachable',
            'details': str(e)
        }), 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
