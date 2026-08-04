"""
Example script to run ShortsMaker.
This version includes alternative methods to fetch Reddit posts using Scrape.do or Apify,
bypassing the need for official Reddit API credentials (client_id and secret_id).
"""

import os
import yaml
import requests
from pathlib import Path
from ShortsMaker import MoviepyCreateVideo, ShortsMaker

# Configuration
SETUP_FILE = "setup.yml"
DEFAULT_REDDIT_URL = "https://www.reddit.com/r/Python/comments/1j36d7a/i_got_tired_of_ai_shorts_scams_so_i_built_my_own/"


def get_reddit_post_via_scrapedo(url: str, output_file: Path) -> bool:
    """
    Fetch Reddit post content using Scrape.do API.
    Uses Reddit's .json endpoint to get structured data without HTML parsing.
    """
    print(f"[Scrape.do] Fetching post: {url}")
    
    # Reddit exposes a JSON endpoint by appending .json to the URL
    json_url = url.rstrip('/') + '.json'
    
    api_url = "https://api.scrape.do/"
    params = {
        "token": os.environ.get("SCRAPEDO_API_KEY"),
        "url": json_url,
        "render": "false"  # No need for JS rendering for JSON
    }
    
    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        # Reddit JSON structure: first element is the post, second is comments
        post_data = data[0]['data']['children'][0]['data']
        
        title = post_data.get('title', 'No Title')
        selftext = post_data.get('selftext', 'No Content')
        
        # Format as expected by ShortsMaker
        script = f"{title}\n\n{selftext}"
        
        # Ensure cache directory exists
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
    Requires APIFY_API_TOKEN environment variable.
    """
    print(f"[Apify] Fetching post: {url}")
    
    try:
        from apify_client import ApifyClient
        
        # Initialize Apify client with token
        client = ApifyClient(os.environ.get("APIFY_API_TOKEN"))
        
        # Use the Reddit Scraper actor
        run = client.actor("apify/reddit-scraper").call(run_input={
            "startUrls": [{"url": url}],
            "maxItems": 1,
            "proxyConfiguration": {"useApifyProxy": True}
        })
        
        # Get results from dataset
        dataset = client.dataset(run["defaultDatasetId"])
        posts = list(dataset.iterate_items())
        
        if not posts:
            print("[Apify] No posts found.")
            return False
            
        post = posts[0]
        title = post.get('title', 'No Title')
        content = post.get('text', post.get('selftext', 'No Content'))
        
        script = f"{title}\n\n{content}"
        
        # Ensure cache directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(script)
            
        print(f"[Apify] Post saved successfully to: {output_file}")
        return True
        
    except ImportError:
        print("[Apify] Error: apify-client package not installed. Run: uv pip install apify-client")
        return False
    except Exception as e:
        print(f"[Apify] Error fetching post: {e}")
        return False


def main():
    # Load configuration
    with open(SETUP_FILE) as f:
        cfg = yaml.safe_load(f)
    
    # Determine output file path
    cache_dir = Path(cfg["cache_dir"])
    record_file = Path(cfg["reddit_post_getter"]["record_file_txt"])
    output_script_path = cache_dir / record_file
    
    # Get Reddit URL from environment or use default
    reddit_url = os.getenv("REDDIT_POST_URL", DEFAULT_REDDIT_URL)
    
    # Determine which scraping method to use based on available API keys
    use_apify = os.getenv("APIFY_API_TOKEN") is not None
    use_scrapedo = os.getenv("SCRAPEDO_API_KEY") is not None
    
    success = False
    
    if use_apify:
        # Try Apify first
        success = get_reddit_post_via_apify(reddit_url, output_script_path)
        if not success:
            print("[Apify] Failed, trying fallback methods...")
            
    if not success and use_scrapedo:
        # Try Scrape.do as fallback or primary
        success = get_reddit_post_via_scrapedo(reddit_url, output_script_path)
        if not success:
            print("[Scrape.do] Failed, trying fallback methods...")
    
    if not success:
        print("❌ All methods failed to fetch Reddit post. Exiting.")
        return
    
    # Continue with audio generation
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
    
    # Create video
    print("Rendering final video...")
    create_video = MoviepyCreateVideo(
        config_file=SETUP_FILE,
        speed_factor=1.0,
    )
    
    output_video_path = os.getenv("OUTPUT_VIDEO_PATH", "assets/output.mp4")
    create_video(output_path=output_video_path)
    create_video.quit()
    
    print(f"✅ Video generated successfully: {output_video_path}")


if __name__ == "__main__":
    main()
