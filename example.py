"""
ShortsMaker Web UI
A minimal native Python web server to interact with the ShortsMaker pipeline.
No external web frameworks (Flask/FastAPI) required.
"""

import os
import yaml
import requests
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# Configuration
SETUP_FILE = "setup.yml"
DEFAULT_REDDIT_URL = "https://www.reddit.com/r/Python/comments/1j36d7a/i_got_tired_of_ai_shorts_scams_so_i_built_my_own/"

# Global state to communicate between background thread and web UI
PIPELINE_STATE = {
    "status": "idle",  # idle, running, completed, error
    "message": "Pronto para gerar.",
    "video_url": None,
    "progress": 0
}

# --- Scrape.do & Apify Functions ---

def get_reddit_post_via_scrapedo(url: str, output_file: Path) -> bool:
    print(f"[Scrape.do] Fetching post: {url}")
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
        print(f"[Scrape.do] Error: {e}")
        return False

def get_reddit_post_via_apify(url: str, output_file: Path) -> bool:
    print(f"[Apify] Fetching post: {url}")
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
        print(f"[Apify] Error: {e}")
        return False

# --- Main Pipeline ---

def run_pipeline(reddit_url: str):
    global PIPELINE_STATE
    PIPELINE_STATE["status"] = "running"
    PIPELINE_STATE["progress"] = 10
    PIPELINE_STATE["message"] = "Iniciando busca do post..."
    
    try:
        # Importação tardia para não travar a inicialização do servidor
        from ShortsMaker import MoviepyCreateVideo, ShortsMaker
        
        with open(SETUP_FILE) as f:
            cfg = yaml.safe_load(f)
        
        cache_dir = Path(cfg["cache_dir"])
        record_file = Path(cfg["reddit_post_getter"]["record_file_txt"])
        output_script_path = cache_dir / record_file
        
        PIPELINE_STATE["message"] = "Buscando post via Scrape.do/Apify..."
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
        PIPELINE_STATE["message"] = "Gerando áudio (TTS)..."
        shorts_maker = ShortsMaker(SETUP_FILE)
        shorts_maker.generate_audio(
            source_txt=script,
            output_audio=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            output_script_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )
        
        PIPELINE_STATE["progress"] = 60
        PIPELINE_STATE["message"] = "Gerando transcrição (WhisperX)..."
        shorts_maker.generate_audio_transcript(
            source_audio_file=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            source_text_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )
        shorts_maker.quit()
        
        PIPELINE_STATE["progress"] = 80
        PIPELINE_STATE["message"] = "Renderizando vídeo final (MoviePy)..."
        create_video = MoviepyCreateVideo(config_file=SETUP_FILE, speed_factor=1.0)
        output_video_path = "assets/output.mp4"
        create_video(output_path=output_video_path)
        create_video.quit()
        
        PIPELINE_STATE["status"] = "completed"
        PIPELINE_STATE["progress"] = 100
        PIPELINE_STATE["message"] = "Vídeo gerado com sucesso!"
        PIPELINE_STATE["video_url"] = "/video"
        print("✅ Vídeo gerado!")
        
    except Exception as e:
        PIPELINE_STATE["status"] = "error"
        PIPELINE_STATE["message"] = f"Erro: {str(e)}"
        print(f"❌ Erro: {e}")

# --- Web Server & UI ---

HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ShortsMaker v2</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0f2f5; color: #1c1e21; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); width: 100%; max-width: 500px; text-align: center; }
        h1 { margin-top: 0; color: #1877f2; }
        input[type="text"] { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ccd0d5; border-radius: 8px; box-sizing: border-box; font-size: 14px; }
        button { background-color: #1877f2; color: white; border: none; padding: 12px 24px; border-radius: 8px; cursor: pointer; font-size: 16px; font-weight: bold; width: 100%; transition: background 0.3s; }
        button:hover { background-color: #166fe5; }
        button:disabled { background-color: #bcc0c4; cursor: not-allowed; }
        .status { margin-top: 20px; padding: 15px; border-radius: 8px; background-color: #e4e6eb; font-weight: 500; }
        .progress-bar { width: 100%; background-color: #e4e6eb; border-radius: 8px; margin-top: 10px; overflow: hidden; }
        .progress-fill { height: 10px; background-color: #42b72a; width: 0%; transition: width 0.5s; }
        .download-link { display: none; margin-top: 20px; text-decoration: none; background-color: #42b72a; color: white; padding: 12px; border-radius: 8px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 ShortsMaker</h1>
        <p>Cole o link do post do Reddit abaixo:</p>
        <input type="text" id="redditUrl" placeholder="https://www.reddit.com/r/..." value="">
        <button id="genBtn" onclick="startGeneration()">Gerar Vídeo</button>
        
        <div class="status" id="statusBox">Status: Pronto</div>
        <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
        
        <a href="#" class="download-link" id="downloadLink" download>Baixar Vídeo MP4</a>
    </div>

    <script>
        let pollInterval;
        
        async function startGeneration() {
            const url = document.getElementById('redditUrl').value;
            const btn = document.getElementById('genBtn');
            const statusBox = document.getElementById('statusBox');
            const progressFill = document.getElementById('progressFill');
            const downloadLink = document.getElementById('downloadLink');
            
            if(!url) { alert("Cole uma URL!"); return; }
            
            btn.disabled = true;
            downloadLink.style.display = 'none';
            statusBox.innerText = "Iniciando...";
            progressFill.style.width = '0%';
            
            // Chama o endpoint /start
            fetch('/start?url=' + encodeURIComponent(url))
            .then(res => res.json())
            .then(data => {
                if(data.status === 'running') {
                    // Começa a checar o status a cada 3 segundos
                    pollInterval = setInterval(checkStatus, 3000);
                } else {
                    statusBox.innerText = "Erro: " + data.message;
                    btn.disabled = false;
                }
            })
            .catch(err => {
                statusBox.innerText = "Erro de conexão.";
                btn.disabled = false;
            });
        }
        
        async function checkStatus() {
            const res = await fetch('/status');
            const data = await res.json();
            
            const statusBox = document.getElementById('statusBox');
            const progressFill = document.getElementById('progressFill');
            const downloadLink = document.getElementById('downloadLink');
            const btn = document.getElementById('genBtn');
            
            statusBox.innerText = "Status: " + data.message;
            progressFill.style.width = data.progress + '%';
            
            if(data.status === 'completed') {
                clearInterval(pollInterval);
                downloadLink.href = data.video_url;
                downloadLink.style.display = 'block';
                btn.disabled = false;
                statusBox.innerText = "✅ " + data.message;
            } else if (data.status === 'error') {
                clearInterval(pollInterval);
                btn.disabled = false;
                statusBox.innerText = "❌ " + data.message;
            }
        }
    </script>
</body>
</html>
"""

class WebUIHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        # Resolve o erro 501 do Render Health Check
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == '/' or path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
            
        elif path == '/start':
            global PIPELINE_STATE
            if PIPELINE_STATE["status"] == "running":
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Já existe um vídeo sendo gerado."}).encode('utf-8'))
                return

            reddit_url = params.get('url', [DEFAULT_REDDIT_URL])[0]
            
            # Reset state
            PIPELINE_STATE = {"status": "running", "message": "Iniciando...", "video_url": None, "progress": 0}
            
            # Start thread
            thread = threading.Thread(target=run_pipeline, args=(reddit_url,), daemon=True)
            thread.start()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "running", "message": "Processo iniciado em background."}).encode('utf-8'))
            
        elif path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(PIPELINE_STATE).encode('utf-8'))
            
        elif path == '/video':
            video_path = "assets/output.mp4"
            if os.path.exists(video_path):
                self.send_response(200)
                self.send_header('Content-type', 'video/mp4')
                self.end_headers()
                with open(video_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Video not found.")
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), WebUIHandler)
    print(f"✅ Web UI Server started on port {port}.")
    server.serve_forever()

if __name__ == "__main__":
    run_server()
