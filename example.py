"""
Example script to run ShortsMaker.
This version includes a minimal HTTP server to satisfy Render's Web Service port requirement,
and fetches Reddit posts using Scrape.do or Apify.
"""

import os
import yaml
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Configuration
SETUP_FILE = "setup.yml"
DEFAULT_REDDIT_URL = "https://www.reddit.com/r/Python/comments/1j36d7a/i_got_tired_of_ai_shorts_scams_so_i_built_my_own/"


def get_reddit_post_via_scrapedo(url: str, output_file: Path) -> bool:
    """
    Fetch Reddit post content using Scrape.do API.
    Uses Reddit's .json endpoint to get structured data without HTML parsing.
    """
    print(f"[Scrape.do] Fetching post: {url}")
    
    json_url = url.rstrip('/') + '.json'
    api_url = "https://api.scrape.do/"
    
    params = {
        "token": os.environ.get("SCRAPEDO_API_KEY"),
        "url": json_url,
        "render": "false"
    }
    
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
            
        print(f"[Scrape.do] Post saved successfully to: {output_file}")
        return True
        
    except Exception as e:
        print(f"[Scrape.do] Error fetching post: {e}")
        return False


def get_reddit_post_via_apify(url: str, output_file: Path) -> bool:
    """
    Fetch Reddit post content using Apify Reddit Scraper.
    """
    print(f"[Apify] Fetching post: {url}")
    
    try:
        from apify_client import ApifyClient
        
        client = ApifyClient(os.environ.get("APIFY_API_TOKEN"))
        
        run = client.actor("apify/reddit-scraper").call(run_input={
            "startUrls": [{"url": url}],
            "maxItems": 1,
            "proxyConfiguration": {"useApifyProxy": True}
        })
        
        dataset = client.dataset(run["defaultDatasetId"])
        posts = list(dataset.iterate_items())
        
        if not posts:
            print("[Apify] No posts found.")
            return False
            
        post = posts[0]
        title = post.get('title', 'No Title')
        content = post.get('text', post.get('selftext', 'No Content'))
        
        script = f"{title}\n\n{content}"
        
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(script)
            
        print(f"[Apify] Post saved successfully to: {output_file}")
        return True
        
    except ImportError:
        print("[Apify] Error: apify-client package not installed.")
        return False
    except Exception as e:
        print(f"[Apify] Error fetching post: {e}")
        return False


def run_pipeline():
    """
    Main pipeline execution. Runs in a background thread.
    """
    try:
        # IMPORTANTE: Importamos o ShortsMaker AQUI, não no topo do arquivo!
        # Isso permite que o servidor HTTP abra a porta imediatamente antes do torch carregar.
        from ShortsMaker import MoviepyCreateVideo, ShortsMaker
        
        print("Iniciando pipeline de geração de vídeo...")
        with open(SETUP_FILE) as f:
            cfg = yaml.safe_load(f)
        
        cache_dir = Path(cfg["cache_dir"])
        record_file = Path(cfg["reddit_post_getter"]["record_file_txt"])
        output_script_path = cache_dir / record_file
        
        reddit_url = os.getenv("REDDIT_POST_URL", DEFAULT_REDDIT_URL)
        
        use_apify = os.getenv("APIFY_API_TOKEN") is not None
        use_scrapedo = os.getenv("SCRAPEDO_API_KEY") is not None
        
        success = False
        
        if use_apify:
            success = get_reddit_post_via_apify(reddit_url, output_script_path)
            if not success:
                print("[Apify] Failed, trying fallback methods...")
                
        if not success and use_scrapedo:
            success = get_reddit_post_via_scrapedo(reddit_url, output_script_path)
            if not success:
                print("[Scrape.do] Failed, trying fallback methods...")
        
        if not success:
            print("❌ All methods failed to fetch Reddit post. Exiting pipeline.")
            return
        
        with open(output_script_path) as f:
            script = f.read()
        
        print("Generating audio...")
        shorts_maker = ShortsMaker(SETUP_FILE)
        shorts_maker.generate_audio(
            source_txt=script,
            output_audio=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            output_script_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )
        
        print("Generating transcription...")
        shorts_maker.generate_audio_transcript(
            source_audio_file=f"{cfg['cache_dir']}/{cfg['audio']['output_audio_file']}",
            source_text_file=f"{cfg['cache_dir']}/{cfg['audio']['output_script_file']}",
        )
        shorts_maker.quit()
        
        print("Rendering final video...")
        create_video = MoviepyCreateVideo(
            config_file=SETUP_FILE,
            speed_factor=1.0,
        )
        
        output_video_path = os.getenv("OUTPUT_VIDEO_PATH", "assets/output.mp4")
        create_video(output_path=output_video_path)
        create_video.quit()
        
        print(f"✅ Video generated successfully: {output_video_path}")
        
    except Exception as e:
        print(f"❌ Error during pipeline execution: {e}")


class SimpleHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler just to keep Render happy with an open port."""
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"ShortsMaker is running. Video generation pipeline is executing in the background. Check logs for progress.")

def run_server():
    """Starts the minimal HTTP server."""
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    print(f"✅ HTTP Server started on port {port}. Keeping process alive for Render.")
    server.serve_forever()

if __name__ == "__main__":
    # 1. Inicia o servidor HTTP PRIMEIRO na thread principal para abrir a porta imediatamente
    # 2. O pipeline de geração de vídeo roda em background
    
    pipeline_thread = threading.Thread(target=run_pipeline, daemon=True)
    pipeline_thread.start()
    
    run_server()
