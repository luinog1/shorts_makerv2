"""
ShortsMaker Advanced Web UI (Flask) - VERSAO CORRIGIDA

Correcoes principais:
1. /start agora eh POST + GET (nao so GET) e loga via print() e logging
2. checkStatus() no frontend trata 'idle' como sinal de reset do servidor
3. startGen() tem .catch() que reabilita o botao em caso de erro
4. /reset endpoint para destravar estado preso
5. Auto-reset: se status='running' ha mais de 15 min, marca como erro
6. Botao "Reiniciar UI" no frontend para destravar manualmente
7. Polling com backoff exponencial ate 10s (evita hammer)
8. /status retorna timestamp do inicio do run para diagnostico
"""

import os
import time
import yaml
import requests
import threading
import json
import logging
from pathlib import Path
from flask import Flask, request, jsonify, send_file, Response, render_template_string

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("shortsmaker")

app = Flask(__name__)

SETUP_FILE = "setup.yml"
DEFAULT_SUBREDDIT = "AskReddit"

# Tempo maximo de execucao antes de auto-marcar como erro (15 min)
RUN_TIMEOUT_SEC = 15 * 60

PIPELINE_STATE = {
    "status": "idle",
    "message": "Pronto para gerar.",
    "video_url": None,
    "progress": 0,
    "logs": [],
    "started_at": None,  # timestamp Unix do inicio do run atual
    "thread_alive": False,
}

# Lock para escrita concorrente no estado
_state_lock = threading.Lock()


def _set_state(**kwargs):
    """Atualiza PIPELINE_STATE de forma thread-safe."""
    with _state_lock:
        PIPELINE_STATE.update(kwargs)


def _append_log(msg: str):
    with _state_lock:
        PIPELINE_STATE["logs"].append(msg)
        # Limita a 200 linhas para nao estourar memoria
        if len(PIPELINE_STATE["logs"]) > 200:
            PIPELINE_STATE["logs"] = PIPELINE_STATE["logs"][-200:]


def get_reddit_post_via_scrapedo(url: str, output_file: Path) -> bool:
    _append_log(f"[Scrape.do] Buscando dados...")

    if not url:
        json_url = f"https://www.reddit.com/r/{DEFAULT_SUBREDDIT}/top.json?t=day&limit=1"
        _append_log(f"[Scrape.do] Sem URL. Buscando top post de r/{DEFAULT_SUBREDDIT}...")
    else:
        json_url = url.rstrip('/') + '.json'
        _append_log(f"[Scrape.do] Buscando URL específica...")

    api_url = "https://api.scrape.do/"
    params = {"token": os.environ.get("SCRAPEDO_API_KEY"), "url": json_url, "render": "false"}

    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, list):
            post_data = data[0]['data']['children'][0]['data']
        else:
            post_data = data['data']['children'][0]['data']

        title = post_data.get('title', 'No Title')
        selftext = post_data.get('selftext', 'No Content')

        if not selftext:
            script = title
        else:
            script = f"{title}\n\n{selftext}"

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(script)

        _append_log(f"[Scrape.do] Post encontrado: {title[:50]}...")
        return True

    except Exception as e:
        _append_log(f"[Scrape.do] Erro: {e}")
        return False


def get_reddit_post_via_apify(url: str, output_file: Path) -> bool:
    _append_log(f"[Apify] Buscando...")
    try:
        from apify_client import ApifyClient
        client = ApifyClient(os.environ.get("APIFY_API_TOKEN"))

        if not url:
            run_input = {
                "startUrls": [{"url": f"https://www.reddit.com/r/{DEFAULT_SUBREDDIT}/top/?t=day"}],
                "maxItems": 1,
                "proxyConfiguration": {"useApifyProxy": True}
            }
        else:
            run_input = {
                "startUrls": [{"url": url}],
                "maxItems": 1,
                "proxyConfiguration": {"useApifyProxy": True}
            }

        run = client.actor("apify/reddit-scraper").call(run_input=run_input)
        dataset = client.dataset(run["defaultDatasetId"])
        posts = list(dataset.iterate_items())

        if not posts:
            return False
        post = posts[0]
        title = post.get('title', 'No Title')
        content = post.get('text', post.get('selftext', ''))
        script = f"{title}\n\n{content}" if content else title

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(script)
        return True
    except Exception as e:
        _append_log(f"[Apify] Erro: {e}")
        return False


def run_pipeline(reddit_url: str):
    """Executa o pipeline em background. Thread-safe."""
    log.info("Pipeline iniciado. URL=%r", reddit_url)
    _set_state(
        status="running",
        message="Iniciando...",
        video_url=None,
        progress=0,
        logs=[],
        started_at=time.time(),
        thread_alive=True,
    )

    try:
        # ===================================================================
        # SHIM DE COMPATIBILIDADE torchaudio >= 2.1 vs pyannote.audio < 3.2
        # ===================================================================
        # torchaudio >= 2.1 removeu varios simbolos do namespace raiz que
        # pyannote.audio < 3.2 ainda usa. Esta camada de shim restaura os
        # que sabemos que faltam:
        #   - torchaudio.AudioMetaData    (anotacao de tipo em pyannote .../io.py)
        #   - torchaudio.list_audio_backends()  (chamado em pyannote .../io.py:__init__)
        #   - torchaudio.info()           (fallback caso pyannote use)
        #   - torchaudio.load()           (fallback caso pyannote use)
        #
        # IMPORTANTE: o shim so e necessario porque as versoes instaladas
        # estao incompativeis. A solucao definitiva e pinar:
        #   torch==2.0.1, torchaudio==2.0.2, pyannote.audio==3.0.1
        # Veja CORRECOES-torchaudio.md.
        # ===================================================================
        _append_log("[Pipeline] Aplicando shim de compatibilidade torchaudio...")
        try:
            import torchaudio
            import importlib

            # ---- 1. AudioMetaData ----
            if not hasattr(torchaudio, 'AudioMetaData'):
                log.warning("torchaudio.AudioMetaData ausente. Aplicando shim...")
                _AudioMetaData = None
                for _path in (
                    "torchaudio.backend.common.AudioMetaData",
                    "torchaudio.backend.sox_io_backend.AudioMetaData",
                ):
                    try:
                        _mod_name, _cls_name = _path.rsplit(".", 1)
                        _mod = importlib.import_module(_mod_name)
                        if hasattr(_mod, _cls_name):
                            _AudioMetaData = getattr(_mod, _cls_name)
                            break
                    except Exception:
                        continue
                if _AudioMetaData is None:
                    class _AudioMetaData:
                        def __init__(self, sample_rate=0, num_frames=0,
                                     num_channels=0, bits_per_sample=0,
                                     encoding=""):
                            self.sample_rate = sample_rate
                            self.num_frames = num_frames
                            self.num_channels = num_channels
                            self.bits_per_sample = bits_per_sample
                            self.encoding = encoding
                torchaudio.AudioMetaData = _AudioMetaData
                _append_log("[Pipeline] Shim AudioMetaData aplicado.")

            # ---- 2. list_audio_backends ----
            # torchaudio >= 2.1 removeu do namespace raiz; ainda existe em
            # torchaudio.utils.sox_utils.list_audio_backends() ou via backend.
            if not hasattr(torchaudio, 'list_audio_backends'):
                log.warning("torchaudio.list_audio_backends ausente. Aplicando shim...")

                def _list_audio_backends():
                    """Retorna lista de backends disponiveis (shim)."""
                    backends = []
                    # Tenta os backends conhecidos um a um
                    for _backend in ("ffmpeg", "sox", "soundfile", "sineio"):
                        try:
                            _mod = importlib.import_module(f"torchaudio.backend.{_backend}_backend")
                            # Se importou sem erro, considera disponivel
                            if hasattr(_mod, "load"):
                                backends.append(_backend)
                        except Exception:
                            pass
                    # soundfile e o fallback universal mais comum
                    if not backends:
                        try:
                            import soundfile  # noqa: F401
                            backends.append("soundfile")
                        except ImportError:
                            pass
                    log.info("list_audio_backends() shim -> %s", backends)
                    return backends

                torchaudio.list_audio_backends = _list_audio_backends
                _append_log("[Pipeline] Shim list_audio_backends aplicado.")

            # ---- 3. info() e load() (defensivo, caso pyannote use) ----
            if not hasattr(torchaudio, 'info'):
                def _info(filepath, **kwargs):
                    """Shim: usa soundfile para ler metadados."""
                    import soundfile as sf
                    info_obj = sf.info(filepath)
                    # Retorna um objeto compativel com torchaudio.AudioMetaData
                    return torchaudio.AudioMetaData(
                        sample_rate=info_obj.samplerate,
                        num_frames=info_obj.frames,
                        num_channels=info_obj.channels,
                        bits_per_sample=info_obj.subtype_bitdepth if hasattr(info_obj, 'subtype_bitdepth') else 16,
                        encoding=info_obj.subtype,
                    )
                torchaudio.info = _info

            if not hasattr(torchaudio, 'load'):
                def _load(filepath, **kwargs):
                    """Shim: usa soundfile para carregar audio."""
                    import soundfile as sf
                    import torch
                    data, sr = sf.read(filepath, dtype="float32",
                                       always_2d=True)
                    # data vem como (frames, channels); torch espera (channels, frames)
                    import numpy as np
                    tensor = torch.from_numpy(np.transpose(data))
                    return tensor, sr
                torchaudio.load = _load

            _append_log("[Pipeline] Shim torchaudio completo aplicado com sucesso.")

        except ImportError:
            _append_log("[Pipeline] torchaudio nao instalado ainda - shim pulado.")

        # Imports pesados AQUI (nao no modulo) para o Flask abrir a porta rapido
        log.info("Importando ShortsMaker (pode levar 30-60s)...")
        _append_log("[Pipeline] Importando bibliotecas pesadas...")
        from ShortsMaker import MoviepyCreateVideo, ShortsMaker

        _set_state(progress=10, message="Buscando post...")
        _append_log("[Pipeline] Bibliotecas carregadas.")

        with open(SETUP_FILE) as f:
            cfg = yaml.safe_load(f)

        # ---- VALIDACAO PREVIA DE MODELO WHISPER ----
        # Modelos large-v2/large-v3 precisam de ~3GB RAM; free tier do Render
        # so tem 512MB. Avisar ANTES de tentar carregar (evita OOM kill silencioso).
        _audio_cfg = cfg.get("audio", {}) or {}
        _model = (_audio_cfg.get("model") or "").lower()
        _device = (_audio_cfg.get("device") or "cpu").lower()
        _MODEL_RAM_ESTIMATE = {
            "tiny":   "150 MB",
            "base":   "250 MB",
            "small":  "500 MB",
            "medium": "1.5 GB",
            "large":  "3 GB",
            "large-v2": "3 GB",
            "large-v3": "3 GB",
        }
        _ram_est = _MODEL_RAM_ESTIMATE.get(_model, "desconhecido")
        _append_log(f"[Pipeline] Modelo Whisper: '{_model}' (RAM estimada: {_ram_est})")
        _append_log(f"[Pipeline] Device: {_device}")
        if _model.startswith("large"):
            _append_log(
                "[Pipeline] ⚠ AVISO: modelo 'large' em CPU exige ~3GB RAM. "
                "Render free tier tem 512MB — OOM kill provável. "
                "Troque para 'tiny', 'base' ou 'small' no setup.yml."
            )
            log.warning("Modelo Whisper '%s' provavelmente vai causar OOM no Render free tier.", _model)

        # ---- LOG DE MEMORIA ATUAL ----
        try:
            import psutil
            _mem = psutil.virtual_memory()
            _append_log(
                f"[Pipeline] Memoria do container: "
                f"{_mem.total // (1024*1024)}MB total, "
                f"{_mem.available // (1024*1024)}MB disponivel "
                f"({_mem.percent}% em uso)"
            )
            # Alerta critico se disponivel < 1GB e modelo for pesado
            if _mem.available < 1024*1024*1024 and _model in ("medium", "large", "large-v2", "large-v3"):
                raise Exception(
                    f"Memória insuficiente para o modelo '{_model}'. "
                    f"Disponível: {_mem.available // (1024*1024)}MB. "
                    f"Necessário estimado: {_ram_est}. "
                    f"Troque o modelo no setup.yml para 'tiny', 'base' ou 'small', "
                    f"ou faça upgrade do plano do Render (atual: free tier 512MB)."
                )
        except ImportError:
            _append_log("[Pipeline] psutil nao instalado - memoria nao reportada.")
        except Exception as _e:
            # Se for o erro critico de memoria, propaga; senao so loga
            if "Memória insuficiente" in str(_e):
                raise
            _append_log(f"[Pipeline] Nao foi possivel checar memoria: {_e}")

        cache_dir = Path(cfg["cache_dir"])
        record_file = Path(cfg["reddit_post_getter"]["record_file_txt"])
        output_script_path = cache_dir / record_file

        success = False
        if os.getenv("APIFY_API_TOKEN"):
            success = get_reddit_post_via_apify(reddit_url, output_script_path)
        if not success and os.getenv("SCRAPEDO_API_KEY"):
            success = get_reddit_post_via_scrapedo(reddit_url, output_script_path)

        if not success:
            raise Exception("Falha ao obter post do Reddit. Verifique SCRAPEDO_API_KEY ou APIFY_API_TOKEN.")

        with open(output_script_path) as f:
            script = f.read()

        _set_state(progress=30, message="Gerando áudio...")
        shorts_maker = ShortsMaker(SETUP_FILE)
        shorts_maker.generate_audio(
            source_txt=script,
            output_audio=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            output_script_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )

        _set_state(progress=60, message="Gerando transcrição...")
        _append_log("[Pipeline] Chamando generate_audio_transcript...")
        shorts_maker.generate_audio_transcript(
            source_audio_file=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            source_text_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )

        # ---- DETECCAO DE FALHA SILENCIOSA ----
        # O decorator @retry do ShortsMaker engole excecoes e retorna None em
        # alguns caminhos. Se word_transcript/line_transcript estao vazios,
        # o pipeline travaria no create_video() abaixo. Abortamos aqui.
        _wt = getattr(shorts_maker, 'word_transcript', None)
        _lt = getattr(shorts_maker, 'line_transcript', None)
        if not _wt and not _lt:
            raise Exception(
                "Transcrição vazia — generate_audio_transcript falhou silenciosamente. "
                "Verifique se o modelo WhisperX foi baixado (HUGGING_FACE_HUB_TOKEN) "
                "e se o cache/ não está corrompido."
            )
        _append_log("[Pipeline] Transcrição gerada com sucesso.")
        shorts_maker.quit()

        _set_state(progress=80, message="Renderizando vídeo...")
        create_video = MoviepyCreateVideo(config_file=SETUP_FILE, speed_factor=1.0)
        output_video_path = "assets/output.mp4"
        create_video(output_path=output_video_path)
        create_video.quit()

        _set_state(
            status="completed",
            progress=100,
            message="Vídeo gerado com sucesso!",
            video_url="/video",
        )
        log.info("Pipeline concluido com sucesso.")

    except Exception as e:
        log.exception("Pipeline falhou:")
        _set_state(status="error", message=f"Erro: {str(e)}")
        _append_log(f"Erro crítico: {str(e)}")
    finally:
        _set_state(thread_alive=False)


def _watchdog():
    """Background thread que detecta runs presos (>RUN_TIMEOUT_SEC)."""
    while True:
        time.sleep(30)
        with _state_lock:
            if (PIPELINE_STATE["status"] == "running"
                and PIPELINE_STATE["started_at"]
                and time.time() - PIPELINE_STATE["started_at"] > RUN_TIMEOUT_SEC):
                PIPELINE_STATE["status"] = "error"
                PIPELINE_STATE["message"] = "Timeout: pipeline excedeu 15 min."
                PIPELINE_STATE["logs"].append(
                    f"[Watchdog] Run travado por mais de {RUN_TIMEOUT_SEC}s. Marcando como erro."
                )
                PIPELINE_STATE["thread_alive"] = False
                log.error("Watchdog: pipeline travado detectado e resetado.")


# Inicia watchdog
threading.Thread(target=_watchdog, daemon=True).start()


# ============ ROTAS ============

@app.route('/')
def home():
    try:
        with open(SETUP_FILE, 'r') as f:
            setup_content = f.read()
    except FileNotFoundError:
        try:
            with open("example.setup.yml", 'r') as f:
                setup_content = f.read()
        except Exception:
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
        log.info("Config salva com sucesso.")
        return jsonify({"status": "success", "message": "Configuração salva!"})
    except Exception as e:
        log.exception("Erro ao salvar config:")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/start', methods=['GET', 'POST'])
def start_pipeline():
    """Inicia o pipeline. Aceita GET (compat) e POST (preferido)."""
    log.info("Requisicao /start recebida. status_atual=%s", PIPELINE_STATE["status"])

    if PIPELINE_STATE["status"] == "running":
        log.warning("/start bloqueado: pipeline ja rodando.")
        return jsonify({"status": "error", "message": "Já está rodando."}), 400

    # Aceita URL via query string (GET) ou form/json (POST)
    reddit_url = ""
    if request.method == "POST":
        if request.is_json:
            reddit_url = (request.get_json(silent=True) or {}).get('url', '').strip()
        else:
            reddit_url = (request.form.get('url') or '').strip()
    if not reddit_url:
        reddit_url = request.args.get('url', '').strip()

    log.info("Iniciando thread para URL=%r", reddit_url)
    thread = threading.Thread(target=run_pipeline, args=(reddit_url,), daemon=True)
    thread.start()
    return jsonify({"status": "success", "message": "Processo iniciado."})


@app.route('/status')
def get_status():
    """Retorna o estado atual + uptime do run."""
    with _state_lock:
        state = dict(PIPELINE_STATE)
    if state.get("started_at") and state["status"] == "running":
        state["uptime_sec"] = int(time.time() - state["started_at"])
    else:
        state["uptime_sec"] = 0
    return jsonify(state)


@app.route('/reset', methods=['POST', 'GET'])
def reset_state():
    """Reseta o estado para idle. Nao mata a thread, mas sinaliza erro no log."""
    log.info("/reset acionado. Estado anterior: %s", PIPELINE_STATE["status"])
    _set_state(
        status="idle",
        message="Estado resetado. Pronto para gerar.",
        video_url=None,
        progress=0,
        logs=[],
        started_at=None,
        thread_alive=False,
    )
    return jsonify({"status": "success", "message": "Estado resetado."})


@app.route('/video')
def download_video():
    video_path = "assets/output.mp4"
    if os.path.exists(video_path):
        return send_file(video_path, as_attachment=True)
    return "Not found", 404


@app.route('/health')
def health():
    """Endpoint de health check leve para o Render."""
    return jsonify({"ok": True, "status": PIPELINE_STATE["status"]})


# ============ HTML/JS ============

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
        button:disabled { background: #ccc; cursor: not-allowed; }
        .status-area { margin-top: 20px; padding: 15px; background: #e4e6eb; border-radius: 4px; }
        .progress { height: 8px; background: #ccc; border-radius: 4px; margin-top: 10px; overflow: hidden; }
        .progress-bar { height: 100%; background: #42b72a; width: 0%; transition: width 0.3s; }
        .logs { margin-top: 15px; padding: 10px; background: #000; color: #0f0; height: 200px; overflow-y: scroll; font-family: monospace; font-size: 12px; border-radius: 4px; white-space: pre-wrap; }
        .download-btn { background: #42b72a; text-decoration: none; display: inline-block; padding: 10px 20px; color: #fff; border-radius: 4px; }
        .hint { font-size: 12px; color: #666; margin-top: -10px; margin-bottom: 15px; }
        .reset-btn { background: #dc3545; }
        .reset-btn:hover { background: #c82333; }
        .error-banner { background: #f8d7da; color: #721c24; padding: 10px; border-radius: 4px; margin-top: 10px; display: none; }
        .uptime { font-size: 11px; color: #888; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 ShortsMaker v2 UI</h1>

        <div class="card">
            <h3>1. Configurações (setup.yml)</h3>
            <span class="hint">Altere vozes, vídeos de fundo e velocidade aqui.</span>
            <textarea id="configText">{{ setup_content }}</textarea>
            <button onclick="saveConfig()" style="background: #ccc; color: black;">Salvar Config</button>

            <hr style="margin: 20px 0;">

            <h3>2. Gerar Vídeo</h3>
            <label>URL do Reddit (Opcional):</label>
            <span class="hint">Deixe em branco para buscar o top post do dia de r/AskReddit automaticamente.</span>
            <input type="text" id="redditUrl" placeholder="https://reddit.com/r/...">
            <button id="genBtn" onclick="startGen()">Iniciar Geração</button>
            <button id="resetBtn" onclick="resetUI()" class="reset-btn">Reiniciar UI</button>

            <div class="error-banner" id="errorBanner"></div>

            <div class="status-area" id="statusArea">
                Status: <span id="statusText">{{ state.message }}</span>
                <div class="uptime" id="uptime"></div>
                <div class="progress"><div class="progress-bar" id="progressBar" style="width: {{ state.progress }}%;"></div></div>
            </div>

            <div id="downloadArea" style="display: none; margin-top: 15px;">
                <a href="/video" class="download-btn">⬇ Baixar Vídeo MP4</a>
            </div>

            <div class="logs" id="logArea"></div>
        </div>
    </div>

    <script>
        let pollInt = null;
        let pollDelay = 2000;  // comeca em 2s, faz backoff ate 10s

        function showError(msg) {
            const b = document.getElementById('errorBanner');
            b.innerText = '⚠ ' + msg;
            b.style.display = 'block';
            console.error('[ShortsMaker]', msg);
            setTimeout(() => { b.style.display = 'none'; }, 8000);
        }

        function clearError() {
            document.getElementById('errorBanner').style.display = 'none';
        }

        function saveConfig() {
            const config = document.getElementById('configText').value;
            const formData = new FormData();
            formData.append('config_text', config);

            fetch('/save_config', { method: 'POST', body: formData })
            .then(r => r.json())
            .then(d => {
                if (d.status === 'success') clearError();
                alert(d.message);
            })
            .catch(err => {
                showError('Falha ao salvar config: ' + err);
                alert('Erro ao salvar: ' + err);
            });
        }

        function startGen() {
            clearError();
            const url = document.getElementById('redditUrl').value || "";
            const btn = document.getElementById('genBtn');
            btn.disabled = true;
            btn.innerText = 'Iniciando...';

            // POST com JSON (preferido); fallback para GET se falhar
            fetch('/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url: url })
            })
            .then(r => {
                if (!r.ok) {
                    return r.json().then(d => { throw new Error(d.message || ('HTTP ' + r.status)); });
                }
                return r.json();
            })
            .then(d => {
                if (d.status === 'success') {
                    btn.innerText = 'Gerando...';
                    pollDelay = 2000;
                    startPolling();
                } else {
                    showError(d.message);
                    alert(d.message);
                    btn.disabled = false;
                    btn.innerText = 'Iniciar Geração';
                }
            })
            .catch(err => {
                console.warn('POST /start falhou, tentando GET:', err);
                // Fallback GET (compatibilidade)
                fetch('/start?url=' + encodeURIComponent(url))
                    .then(r => r.json())
                    .then(d => {
                        if (d.status === 'success') {
                            btn.innerText = 'Gerando...';
                            pollDelay = 2000;
                            startPolling();
                        } else {
                            showError(d.message);
                            alert(d.message);
                            btn.disabled = false;
                            btn.innerText = 'Iniciar Geração';
                        }
                    })
                    .catch(err2 => {
                        showError('Não foi possível iniciar: ' + err2);
                        alert('Não foi possível iniciar: ' + err2);
                        btn.disabled = false;
                        btn.innerText = 'Iniciar Geração';
                    });
            });
        }

        function startPolling() {
            if (pollInt) clearInterval(pollInt);
            pollInt = setInterval(checkStatus, pollDelay);
            checkStatus();  // chama imediatamente
        }

        function checkStatus() {
            fetch('/status', { cache: 'no-store' })
            .then(r => {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(d => {
                document.getElementById('statusText').innerText = d.message + ' [' + d.status + ']';
                document.getElementById('progressBar').style.width = d.progress + '%';

                if (d.uptime_sec > 0) {
                    document.getElementById('uptime').innerText = '⏱ ' + d.uptime_sec + 's decorridos';
                } else {
                    document.getElementById('uptime').innerText = '';
                }

                const logs = (d.logs || []).join('\\n');
                const logArea = document.getElementById('logArea');
                logArea.innerText = logs;
                logArea.scrollTop = logArea.scrollHeight;

                // ---- CRITICO: tratar idle como reset ----
                // Se voltar 'idle' enquanto estamos pollando, significa que o
                // servidor reiniciou. Precisamos parar o polling e reabilitar o botao.
                if (d.status === 'idle') {
                    console.log('[ShortsMaker] Estado idle detectado. Parando polling.');
                    stopPolling();
                    document.getElementById('genBtn').disabled = false;
                    document.getElementById('genBtn').innerText = 'Iniciar Geração';
                    return;
                }

                if (d.status === 'completed' || d.status === 'error') {
                    stopPolling();
                    const btn = document.getElementById('genBtn');
                    btn.disabled = false;
                    btn.innerText = 'Iniciar Geração';
                    if (d.status === 'completed') {
                        document.getElementById('downloadArea').style.display = 'block';
                    } else {
                        showError(d.message || 'Pipeline falhou.');
                    }
                } else {
                    // running: aumenta progresso visual no botao
                    document.getElementById('genBtn').innerText = 'Gerando ' + d.progress + '%';
                    // backoff gradual ate 10s (nao martela o servidor)
                    if (pollDelay < 10000) {
                        pollDelay = Math.min(10000, pollDelay + 500);
                        if (pollInt) {
                            clearInterval(pollInt);
                            pollInt = setInterval(checkStatus, pollDelay);
                        }
                    }
                }
            })
            .catch(err => {
                console.error('[ShortsMaker] Erro no checkStatus:', err);
                // Nao para o polling automaticamente — pode ser glitch de rede.
                // Apenas loga. Se persistir por muito tempo, o watchdog do backend zera.
            });
        }

        function stopPolling() {
            if (pollInt) {
                clearInterval(pollInt);
                pollInt = null;
            }
            pollDelay = 2000;
        }

        function resetUI() {
            if (!confirm('Resetar o estado do servidor? Útil se o pipeline travou.')) return;
            fetch('/reset', { method: 'POST' })
            .then(r => r.json())
            .then(d => {
                clearError();
                stopPolling();
                document.getElementById('genBtn').disabled = false;
                document.getElementById('genBtn').innerText = 'Iniciar Geração';
                document.getElementById('downloadArea').style.display = 'none';
                checkStatus();
                alert(d.message);
            })
            .catch(err => showError('Reset falhou: ' + err));
        }

        // ---- Inicializacao ----
        // Se a pagina carregar com status='running' (refresh durante um run),
        // retoma o polling automaticamente.
        window.addEventListener('load', () => {
            const initialStatus = {{ state.status | tojson }};
            if (initialStatus === 'running') {
                document.getElementById('genBtn').disabled = true;
                document.getElementById('genBtn').innerText = 'Gerando...';
                startPolling();
            }
        });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    log.info("Iniciando servidor Flask na porta %d", port)
    app.run(host="0.0.0.0", port=port)
