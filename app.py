import flask
from flask import Flask, jsonify, request, render_template_string
from flask_socketio import SocketIO, emit
import json
import subprocess
import threading
import time
import os
import shutil
from datetime import datetime
import eventlet

# Monkey patch to allow WebSocket to work with subprocess
eventlet.monkey_patch()

# Inicjalizacja aplikacji i SocketIO z eventlet
app = Flask(__name__, template_folder='.')
app.config['SECRET_KEY'] = 'twoj-sekretny-klucz!' # Zmień na cokolwiek
socketio = SocketIO(app, async_mode='eventlet')

# Globalne słowniki do przechowywania stanu
SERVERS = {}
READ_THREADS = {} # Słownik na wątki czytające output konsoli

# --- Funkcje pomocnicze ---

def stream_output(name):
    """Czyta output procesu serwera i wysyła go przez WebSocket."""
    server = SERVERS.get(name)
    if not server or not server.get('process'):
        return

    process = server['process']
    # Czytaj stdout linia po linii
    for line in iter(process.stdout.readline, ''):
        clean_line = line.strip()
        # Wysyłaj dane w kontekście aplikacji, używając eventlet-safe emit
        socketio.emit('console_output', {'server': name, 'data': clean_line})
        socketio.sleep(0.01) # Daj eventletowi czas na przetworzenie innych zadań
    
    process.stdout.close()
    print(f"Zakończono strumieniowanie dla serwera {name}.")


def load_config():
    """Wczytuje konfigurację serwerów z pliku config.json."""
    global SERVERS
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        for server_config in config.get('servers', []):
            name = server_config['name']
            if name not in SERVERS:
                SERVERS[name] = {
                    'config': server_config, 'process': None,
                    'status': 'Zatrzymany', 'pid': None
                }
        print("Konfiguracja wczytana pomyślnie.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"BŁĄD wczytywania config.json: {e}")
        SERVERS = {}

def start_server(name):
    """Uruchamia serwer i wątek do czytania jego konsoli."""
    if name in SERVERS and SERVERS[name]['status'] in ['Zatrzymany', 'Błąd']:
        server = SERVERS[name]
        server['status'] = 'Uruchamianie'
        socketio.emit('status_update', {'name': name, 'status': 'Uruchamianie'})
        
        config, path = server['config'], server['config']['path']
        command = []
        if 'start_script' in config:
            command = [os.path.join(path, config['start_script'])]
        else:
            command = ['java'] + config.get('java_args', []) + ['-jar', config['jar_file'], 'nogui']

        if not command:
            server['status'] = 'Błąd'; return False
        
        process = subprocess.Popen(
            command, cwd=path, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace'
        )
        
        server.update({'process': process, 'pid': process.pid, 'status': 'Uruchomiony'})
        socketio.emit('status_update', {'name': name, 'status': 'Uruchomiony'})
        
        # Uruchom wątek do czytania konsoli
        thread = socketio.start_background_task(target=stream_output, name=name)
        READ_THREADS[name] = thread
        
        print(f"Serwer '{name}' uruchomiony (PID: {process.pid}).")
        return True
    return False

def stop_server(name):
    """Zatrzymuje serwer i jego wątek czytający."""
    if name in SERVERS and SERVERS[name]['status'] == 'Uruchomiony':
        server = SERVERS[name]
        process = server['process']
        
        stop_command = 'end' if 'proxy' in name.lower() else 'stop'
        print(f"Wysyłanie komendy '{stop_command}' do '{name}'...")
        try:
            process.stdin.write(f'{stop_command}\n')
            process.stdin.flush()
        except (IOError, ValueError) as e:
            print(f"Błąd przy wysyłaniu komendy: {e}")

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            print(f"Serwer '{name}' nie odpowiedział. Wymuszanie zamknięcia.")
            process.kill()
        
        server.update({'process': None, 'pid': None, 'status': 'Zatrzymany'})
        socketio.emit('status_update', {'name': name, 'status': 'Zatrzymany'})
        
        if name in READ_THREADS:
            del READ_THREADS[name]
            
        return True
    return False

def monitor_servers():
    """Monitoruje stan serwerów i restartuje je w razie awarii."""
    while True:
        for name, server in list(SERVERS.items()):
            if server.get('process') and server['process'].poll() is not None:
                if server['status'] != 'Zatrzymany':
                    print(f"Serwer '{name}' zatrzymał się nieoczekiwanie.")
                    server.update({'status': 'Błąd', 'process': None, 'pid': None})
                    socketio.emit('status_update', {'name': name, 'status': 'Błąd'})
                    if server['config'].get('auto_restart', False):
                        print(f"Automatyczne ponowne uruchamianie '{name}'...")
                        start_server(name)
        socketio.sleep(5)

# --- Endpointy API ---
@app.route('/')
def index():
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return render_template_string(f.read())
    except FileNotFoundError:
        return "BŁĄD: Nie znaleziono pliku index.html.", 404

@app.route('/api/servers', methods=['GET'])
def get_servers():
    status_list = [{'name': name, 'status': data['status'], 'auto_restart': data['config'].get('auto_restart', False)} for name, data in SERVERS.items()]
    return jsonify(status_list)

@app.route('/api/servers/<name>/start', methods=['POST'])
def api_start_server(name):
    start_server(name)
    return jsonify({'status': 'success'})

@app.route('/api/servers/<name>/stop', methods=['POST'])
def api_stop_server(name):
    stop_server(name)
    return jsonify({'status': 'success'})

@app.route('/api/servers/<name>/backup', methods=['POST'])
def api_backup_server(name):
    return jsonify({'status': 'error', 'message': 'Backup not implemented in this version yet.'}), 500

# --- Obsługa WebSocket ---
@socketio.on('connect')
def handle_connect():
    print('Klient połączony przez WebSocket')

@socketio.on('disconnect')
def handle_disconnect():
    print('Klient rozłączony')

@socketio.on('send_command')
def handle_send_command(json_data):
    """Odbiera komendę od klienta i wysyła ją do procesu serwera."""
    server_name = json_data.get('server')
    command = json_data.get('command')
    
    server = SERVERS.get(server_name)
    if server and server.get('process') and command:
        print(f"Otrzymano komendę dla '{server_name}': {command}")
        try:
            server['process'].stdin.write(command + '\n')
            server['process'].stdin.flush()
        except (IOError, ValueError) as e:
            print(f"Błąd przy wysyłaniu komendy do '{server_name}': {e}")

# --- Główna część skryptu ---
if __name__ == '__main__':
    print("Inicjalizacja panelu z obsługą konsoli...")
    load_config()
    
    # Uruchom wątek monitorujący w tle
    socketio.start_background_task(target=monitor_servers)
    
    # Auto-start serwerów
    for name, server in SERVERS.items():
        if server['config'].get('auto_start', False):
            start_server(name)
            
    print("\nPanel jest dostępny pod adresem: http://127.0.0.1:5000")
    socketio.run(app, host='0.0.0.0', port=5000)
