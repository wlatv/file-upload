import os
import uuid
import zipfile
import json
import subprocess
import sys
import traceback
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Konfiguratsiya sozlamalari
app.config['UPLOAD_FOLDER'] = os.path.abspath('uploads')
app.config['ALLOWED_EXTENSIONS'] = {'zip', 'py'}
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# Ishchi loyihalar
running_projects = {}
installation_logs = {}

# Ruxsat berilgan fayl kengaytmalari
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Asosiy faylni aniqlash
def determine_main_file(project_path):
    for possible_main in ['bot.py', 'main.py', 'index.py', 'app.py']:
        full_path = os.path.join(project_path, possible_main)
        if os.path.exists(full_path):
            return possible_main
    # Agar standart fayllar topilmasa, birinchi .py faylni topish
    for file in os.listdir(project_path):
        if file.endswith('.py'):
            return file
    return None

# Loyiha konfiguratsiyasini yuklash
def load_project_config(project_id):
    config_path = os.path.join(app.config['UPLOAD_FOLDER'], project_id, 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return json.load(f)
    return {}

# Loyiha konfiguratsiyasini saqlash
def save_project_config(project_id, config):
    config_path = os.path.join(app.config['UPLOAD_FOLDER'], project_id, 'config.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f)

# Unicode xatolarini bartaraf etish uchun log funksiyasi
def safe_log(message, log_file):
    try:
        log_file.write(message + '\n')
    except UnicodeEncodeError:
        log_file.write(message.encode('ascii', 'ignore').decode('ascii') + '\n')

# Bosh sahifa
@app.route('/')
def index():
    projects = []
    uploads_dir = app.config['UPLOAD_FOLDER']
    if not os.path.exists(uploads_dir):
        os.makedirs(uploads_dir)
    
    for project_id in os.listdir(uploads_dir):
        project_path = os.path.join(uploads_dir, project_id)
        if os.path.isdir(project_path):
            config = load_project_config(project_id)
            projects.append({
                'id': project_id,
                'main_file': config.get('main_file', 'not specified'),
                'date': config.get('date', 'unknown'),
                'status': 'Running' if project_id in running_projects else 'Stopped'
            })
    return render_template('index.html', projects=projects)

# Fayl yuklash
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file part'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'})
    
    if file and allowed_file(file.filename):
        project_id = str(uuid.uuid4())[:8]
        project_path = os.path.join(app.config['UPLOAD_FOLDER'], project_id)
        
        # Avvalgi papka bo'lsa o'chirib tashlash
        if os.path.exists(project_path):
            import shutil
            shutil.rmtree(project_path)
        
        os.makedirs(project_path, exist_ok=True)
        
        filename = secure_filename(file.filename)
        file_path = os.path.join(project_path, filename)
        file.save(file_path)
        
        if filename.endswith('.zip'):
            try:
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(project_path)
                os.remove(file_path)
            except zipfile.BadZipFile:
                return jsonify({'status': 'error', 'message': 'Invalid ZIP file'})
        
        # Asosiy faylni aniqlash
        main_file = determine_main_file(project_path)
        
        # Konfiguratsiyani saqlash
        config = {
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'main_file': main_file if main_file else 'not specified',
            'original_filename': filename
        }
        save_project_config(project_id, config)
        
        return jsonify({
            'status': 'success',
            'project_id': project_id,
            'main_file': config['main_file'],
            'absolute_path': os.path.abspath(os.path.join(project_path, config['main_file'])) if main_file else None
        })
    
    return jsonify({'status': 'error', 'message': 'File type not allowed'})

# Paketlarni o'rnatish sahifasi
@app.route('/install/<project_id>')
def install_page(project_id):
    project_path = os.path.join(app.config['UPLOAD_FOLDER'], project_id)
    if not os.path.exists(project_path):
        return redirect(url_for('index'))
    
    config = load_project_config(project_id)
    return render_template('install.html', project_id=project_id, project_name=f"Project {project_id}")

# Paketlarni o'rnatish
@app.route('/install/<project_id>', methods=['POST'])
def install_packages(project_id):
    project_path = os.path.join(app.config['UPLOAD_FOLDER'], project_id)
    if not os.path.exists(project_path):
        return jsonify({'status': 'error', 'message': 'Project not found'})
    
    packages = request.form.get('packages', '')
    if not packages:
        return jsonify({'status': 'error', 'message': 'No packages specified'})
    
    # Log faylini yaratish
    log_path = os.path.join(project_path, 'installation.log')
    installation_logs[project_id] = []
    
    try:
        with open(log_path, 'w', encoding='utf-8') as log_file:
            # Har bir paketni alohida o'rnatish
            for package in packages.split():
                process = subprocess.Popen(
                    [sys.executable, '-m', 'pip', 'install', package],
                    cwd=project_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace'
                )
                
                for line in process.stdout:
                    safe_log(line.strip(), log_file)
                    installation_logs[project_id].append(line.strip())
                
                process.wait()
                
                if process.returncode != 0:
                    safe_log(f"Error installing {package}", log_file)
                    installation_logs[project_id].append(f"Error installing {package}")
            
            safe_log("Installation completed", log_file)
            installation_logs[project_id].append("Installation completed")
        
        return jsonify({'status': 'success', 'message': 'Installation completed'})
    except Exception as e:
        error_msg = f"Installation error: {str(e)}"
        with open(log_path, 'a', encoding='utf-8') as log_file:
            safe_log(error_msg, log_file)
        installation_logs[project_id].append(error_msg)
        return jsonify({'status': 'error', 'message': error_msg})

# Live logni olish
@app.route('/logs/<project_id>')
def get_logs(project_id):
    if project_id not in installation_logs:
        return jsonify({'status': 'error', 'message': 'No logs available'})
    return jsonify({'status': 'success', 'logs': installation_logs[project_id]})

# Loyihani ishga tushirish
@app.route('/start/<project_id>')
def start_project(project_id):
    if project_id in running_projects:
        return jsonify({'status': 'error', 'message': 'Project already running'})
    
    project_path = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], project_id))
    config = load_project_config(project_id)
    
    if not config.get('main_file') or config['main_file'] == 'not specified':
        return jsonify({'status': 'error', 'message': 'Main file not specified'})
    
    main_file_path = os.path.abspath(os.path.join(project_path, config['main_file']))
    
    if not os.path.exists(main_file_path):
        return jsonify({
            'status': 'error',
            'message': f'Main file not found at {main_file_path}',
            'directory_contents': os.listdir(project_path) if os.path.exists(project_path) else 'Directory not found',
            'config': config
        })
    
    try:
        # Log faylini yaratish (Unicode uchun tayyorlangan)
        log_path = os.path.join(project_path, 'output.log')
        
        with open(log_path, 'w', encoding='utf-8') as log_file:
            process = subprocess.Popen(
                [sys.executable, main_file_path],
                cwd=project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # Live log uchun saqlash
            running_projects[project_id] = {
                'process': process,
                'logs': []
            }
            
            # Loglarni real vaqtda o'qish
            def log_reader():
                for line in process.stdout:
                    safe_log(line.strip(), log_file)
                    running_projects[project_id]['logs'].append(line.strip())
            
            import threading
            threading.Thread(target=log_reader).start()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        error_info = {
            'status': 'error',
            'message': str(e),
            'python_path': sys.executable,
            'project_path': project_path,
            'main_file_path': main_file_path,
            'traceback': traceback.format_exc()
        }
        print(f"Error details: {error_info}")
        return jsonify(error_info)

# Loyiha loglarini olish
@app.route('/project_logs/<project_id>')
def get_project_logs(project_id):
    if project_id not in running_projects:
        return jsonify({'status': 'error', 'message': 'Project not running'})
    return jsonify({'status': 'success', 'logs': running_projects[project_id]['logs']})

# Loyihani to'xtatish
@app.route('/stop/<project_id>')
def stop_project(project_id):
    if project_id not in running_projects:
        return jsonify({'status': 'error', 'message': 'Project not running'})
    
    try:
        process = running_projects[project_id]['process']
        process.terminate()
        process.wait(timeout=5)
        del running_projects[project_id]
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# Loyihani o'chirish
@app.route('/delete/<project_id>')
def delete_project(project_id):
    if project_id in running_projects:
        running_projects[project_id]['process'].terminate()
        del running_projects[project_id]
    
    project_path = os.path.join(app.config['UPLOAD_FOLDER'], project_id)
    if os.path.exists(project_path):
        try:
            import shutil
            shutil.rmtree(project_path)
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    
    return jsonify({'status': 'error', 'message': 'Project not found'})

if __name__ == '__main__':
    # Uploads papkasini yaratish
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Serverni ishga tushirish
    app.run(host='0.0.0.0', port=5000, debug=True)