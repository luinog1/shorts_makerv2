"""
ShortsMaker Advanced Web UI (Flask)
"""

import os
import yaml
import requests
import threading
import json
from pathlib import Path
from flask import Flask, request, jsonify, send_file, Response, render_template_string

app = Flask(__name__)

SETUP_FILE = "setup.yml"
DEFAULT_REDDIT_URL = "https://www.reddit.com/r/Python/comments/1j36d7a/i_got_tired_of_ai_shorts_scams_so_i_built_my_own/"

PIPELINE_STATE = {
    "status": "idle",
    "message": "Pronto para gerar.",
    "video_url": None,
    "progress": 0,
    "logs": []
}

def get_reddit_post_via_scrapedo(url: str, output_file: Path) -> bool:
    PIPELINE_STATE["logs"].append(f"[Scrape.do] Buscando: {url}")
    json_url = url.rstrip('/') + '.json'
    api_url = "https://api.scrape.do/"
    params = {"token": os.environ.get("SCRAPEDO_API_KEY"), "url": json_url, "render": "false"}
    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        post_data = data[0]['data']['children'][0]['data']
        title = post_data.get('title', 'No Title')
        selftext = post_data.get('selftext', 'No Content')
        script = f"{title}\n\n{selftext}"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(script)
        return True
    except Exception as e:
        PIPELINE_STATE["logs"].append(f"[Scrape.do] Erro: {e}")
        return False

def get_reddit_post_via_apify(url: str, output_file: Path) -> bool:
    PIPELINE_STATE["logs"].append(f"[Apify] Buscando: {url}")
    try:
        from apify_client import ApifyClient
        client = ApifyClient(os.environ.get("APIFY_API_TOKEN"))
        run = client.actor("apify/reddit-scraper").call(run_input={
            "startUrls": [{"url": url}], "maxItems": 1, "proxyConfiguration": {"useApifyProxy": True}
        })
        dataset = client.dataset(run["defaultDatasetId"])
        posts = list(dataset.iterate_items())
        if not posts: return False
        post = posts[0]
        title = post.get('title', 'No Title')
        content = post.get('text', post.get('selftext', 'No Content'))
        script = f"{title}\n\n{content}"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(script)
        return True
    except Exception as e:
        PIPELINE_STATE["logs"].append(f"[Apify] Erro: {e}")
        return False

def run_pipeline(reddit_url: str):
    global PIPELINE_STATE
    PIPELINE_STATE = {"status": "running", "message": "Iniciando...", "video_url": None, "progress": 0, "logs": []}
    
    try:
        from ShortsMaker import MoviepyCreateVideo, ShortsMaker
        
        PIPELINE_STATE["progress"] = 10
        PIPELINE_STATE["message"] = "Buscando post..."
        
        with open(SETUP_FILE) as f:
            cfg = yaml.safe_load(f)
        
        cache_dir = Path(cfg["cache_dir"])
        record_file = Path(cfg["reddit_post_getter"]["record_file_txt"])
        output_script_path = cache_dir / record_file
        
        success = False
        if os.getenv("APIFY_API_TOKEN"):
            success = get_reddit_post_via_apify(reddit_url, output_script_path)
        if not success and os.getenv("SCRAPEDO_API_KEY"):
            success = get_reddit_post_via_scrapedo(reddit_url, output_script_path)
            
        if not success:
            raise Exception("Falha ao obter post do Reddit.")
            
        with open(output_script_path) as f:
            script = f.read()
            
        PIPELINE_STATE["progress"] = 30
        PIPELINE_STATE["message"] = "Gerando áudio..."
        shorts_maker = ShortsMaker(SETUP_FILE)
        shorts_maker.generate_audio(
            source_txt=script,
            output_audio=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            output_script_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )
        
        PIPELINE_STATE["progress"] = 60
        PIPELINE_STATE["message"] = "Gerando transcrição..."
        shorts_maker.generate_audio_transcript(
            source_audio_file=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            source_text_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )
        shorts_maker.quit()
        
        PIPELINE_STATE["progress"] = 80
        PIPELINE_STATE["message"] = "Renderizando vídeo..."
        create_video = MoviepyCreateVideo(config_file=SETUP_FILE, speed_factor=1.0)
        output_video_path = "assets/output.mp4"
        create_video(output_path=output_video_path)
        create_video.quit()
        
        PIPELINE_STATE["status"] = "completed"
        PIPELINE_STATE["progress"] = 100
        PIPELINE_STATE["message"] = "Vídeo gerado com sucesso!"
        PIPELINE_STATE["video_url"] = "/video"
        
    except Exception as e:
        PIPELINE_STATE["status"] = "error"
        PIPELINE_STATE["message"] = f"Erro: {str(e)}"
        PIPELINE_STATE["logs"].append(f"Erro crítico: {str(e)}")

@app.route('/')
def home():
    try:
        with open(SETUP_FILE, 'r') as f:
            setup_content = f.read()
    except FileNotFoundError:
        try:
            with open("example.setup.yml", 'r') as f:
                setup_content = f.read()
        except:
            setup_content = "setup.yml não encontrado."
            
    return render_template_string(HTML_PAGE, setup_content=setup_content, state=PIPELINE_STATE)

@app.route('/save_config', methods=['POST'])
def save_config():
    if PIPELINE_STATE["status"] == "running":
        return jsonify({"status": "error", "message": "Não é possível salvar enquanto o vídeo está sendo gerado."}), 400
        
    new_config = request.form.get('config_text')
    try:
        with open(SETUP_FILE, 'w') as f:
            f.write(new_config)
        return jsonify({"status": "success", "message": "Configuração salva!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/start')
def start_pipeline():
    global PIPELINE_STATE
    if PIPELINE_STATE["status"] == "running":
        return jsonify({"status": "error", "message": "Já está rodando."}), 400
        
    reddit_url = request.args.get('url', DEFAULT_REDDIT_URL)
    thread = threading.Thread(target=run_pipeline, args=(reddit_url,), daemon=True)
    thread.start()
    return jsonify({"status": "success", "message": "Processo iniciado."})

@app.route('/status')
def get_status():
    return jsonify(PIPELINE_STATE)

@app.route('/video')
def download_video():
    video_path = "assets/output.mp4"
    if os.path.exists(video_path):
        return send_file(video_path, as_attachment=True)
    return "Not found", 404

HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ShortsMaker Advanced UI</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 20px; color: #333; }
        .container { max-width: 900px; margin: 0 auto; display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        h1 { grid-column: 1 / -1; color: #1877f2; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input[type="text"] { width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        textarea { width: 100%; height: 300px; font-family: monospace; padding: 10px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background: #1877f2; color: #fff; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold; margin-right: 10px; }
        button:hover { background: #166fe5; }
        button:disabled { background: #ccc; }
        .status-area { margin-top: 20px; padding: 15px; background: #e4e6eb; border-radius: 4px; }
        .progress { height: 8px; background: #ccc; border-radius: 4px; margin-top: 10px; overflow: hidden; }
        .progress-bar { height: 100%; background: #42b72a; width: 0%; transition: width 0.3s; }
        .logs { margin-top: 15px; padding: 10px; background: #000; color: #0f0; height: 150px; overflow-y: scroll; font-family: monospace; font-size: 12px; border-radius: 4px; }
        .download-btn { background: #42b72a; text-decoration: none; display: inline-block; padding: 10px 20px; color: #fff; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 ShortsMaker v2 UI</h1>
        
        <div class="card">
            <h3>1. Configurações (setup.yml)</h3>
            <textarea id="configText">{{ setup_content }}</textarea>
            <button onclick="saveConfig()" style="background: #ccc; color: black;">Salvar Config</button>
            
            <hr style="margin: 20px 0;">
            
            <h3>2. Gerar Vídeo</h3>
            <label>URL do Reddit:</label>
            <input type="text" id="redditUrl" placeholder="https://reddit.com/...">
            <button id="genBtn" onclick="startGen()">Iniciar Geração</button>
            
            <div class="status-area" id="statusArea">
                Status: <span id="statusText">{{ state.message }}</span>
                <div class="progress"><div class="progress-bar" id="progressBar" style="width: {{ state.progress }}%;"></div></div>
            </div>
            
            <div id="downloadArea" style="display: none; margin-top: 15px;">
                <a href="/video" class="download-btn">⬇ Baixar Vídeo MP4</a>
            </div>
            
            <div class="logs" id="logArea"></div>
        </div>
    </div>

    <script>
        let pollInt;
        
        function saveConfig() {
            const config = document.getElementById('configText').value;
            const formData = new FormData();
            formData.append('config_text', config);
            
            fetch('/save_config', { method: 'POST', body: formData })
            .then(r => r.json())
            .then(d => alert(d.message))
            .catch(err => alert("Erro ao salvar."));
        }
        
        function startGen() {
            const url = document.getElementById('redditUrl').value || "";
            const btn = document.getElementById('genBtn');
            btn.disabled = true;
            
            fetch('/start?url=' + encodeURIComponent(url))
            .then(r => r.json())
            .then(d => {
                if(d.status === 'success') {
                    pollInt = setInterval(checkStatus, 2000);
                } else {
                    alert(d.message);
                    btn.disabled = false;
                }
            });
        }
        
        function checkStatus() {
            fetch('/status')
            .then(r => r.json())
            .then(d => {
                document.getElementById('statusText').innerText = d.message;
                document.getElementById('progressBar').style.width = d.progress + '%';
                
                const logs = d.logs.join('\\n');
                document.getElementById('logArea').innerText = logs;
                document.getElementById('logArea').scrollTop = document.getElementById('logArea').scrollHeight;
                
                if(d.status === 'completed' || d.status === 'error') {
                    clearInterval(pollInt);
                    document.getElementById('genBtn').disabled = false;
                    if(d.status === 'completed') {
                        document.getElementById('downloadArea').style.display = 'block';
                    }
                }
            });
        }
        
        if({{ state.status | tojson }} === 'running') {
            document.getElementById('genBtn').disabled = true;
            pollInt = setInterval(checkStatus, 2000);
        }
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
