import os
import time
import datetime
import subprocess
import requests
import re
import asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CONTACT = "@Kaito_3_2"
CATBOX_UPLOAD = "https://catbox.moe/user/api.php"
COOKIES_PATH = "/app/cookies.txt"

# ─────────────────────────────
# FASTAPI APP
# ─────────────────────────────
app = FastAPI(title="⚡ Sudeep Music API")

# MongoDB
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["MusicAPI_DB1"]
videos_col = db["videos_cachet"]
keys_col = db["api_users"]

# ⚡ ULTRA-FAST RAM CACHE
RAM_CACHE = {}

# ─────────────────────────────
# CORE FUNCTIONS - SIMPLE & WORKING
# ─────────────────────────────
def extract_video_id(q: str):
    """Extract video ID from any input"""
    if not q:
        return None
    
    q = q.strip()
    
    # Direct video ID (11 chars)
    if len(q) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', q):
        return q
    
    # URL patterns
    if "youtube.com/watch?v=" in q:
        match = re.search(r'v=([a-zA-Z0-9_-]{11})', q)
        if match:
            return match.group(1)
    
    if "youtu.be/" in q:
        match = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', q)
        if match:
            return match.group(1)
    
    return None

def format_time(seconds):
    """Convert seconds to MM:SS"""
    if not seconds:
        return "0:00"
    try:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"
    except:
        return "0:00"

def quick_search(query: str):
    """Simple and reliable search"""
    try:
        # Use yt-dlp for searching
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Search for the video
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            
            if info and 'entries' in info and info['entries']:
                video = info['entries'][0]
                return {
                    "id": video.get('id'),
                    "title": video.get('title', 'Unknown Title'),
                    "duration": format_time(video.get('duration'))
                }
    except Exception as e:
        print(f"Search error: {e}")
    
    return None

def get_video_info(video_id: str):
    """Get video info by ID"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            return {
                "id": video_id,
                "title": info.get('title', f'Video {video_id}'),
                "duration": format_time(info.get('duration'))
            }
    except:
        return None

async def verify_key_fast(key: str):
    """Simple API key verification"""
    try:
        doc = await keys_col.find_one({"api_key": key, "active": True})
        if not doc:
            return False, "Invalid API key"
        
        # Check expiry
        if time.time() > doc.get("expires_at", 0):
            return False, "API key expired"
        
        # Check daily limit
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if doc.get("last_reset") != today:
            await keys_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"used_today": 0, "last_reset": today}}
            )
            used_today = 0
        else:
            used_today = doc.get("used_today", 0)
        
        if used_today >= doc.get("daily_limit", 50):
            return False, "Daily limit exceeded"
        
        # Increment counter
        await keys_col.update_one(
            {"_id": doc["_id"]},
            {"$inc": {"used_today": 1}}
        )
        
        return True, None
    except Exception as e:
        return False, f"Verification error: {str(e)}"

def download_video_simple(video_id: str):
    """Download video with minimal options"""
    try:
        out_file = f"/tmp/{video_id}.mp4"
        
        cmd = [
            "yt-dlp",
            "-f", "best[height<=480]",
            "--merge-output-format", "mp4",
            "-o", out_file,
            f"https://www.youtube.com/watch?v={video_id}"
        ]
        
        # Add cookies if exists
        if os.path.exists(COOKIES_PATH):
            cmd.insert(1, "--cookies")
            cmd.insert(2, COOKIES_PATH)
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        
        if os.path.exists(out_file):
            return out_file
        else:
            print(f"Download failed: {result.stderr}")
            return None
    except Exception as e:
        print(f"Download error: {e}")
        return None

def upload_to_catbox(file_path: str):
    """Upload to catbox"""
    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                CATBOX_UPLOAD,
                files={"fileToUpload": f},
                timeout=60
            )
        
        if response.status_code == 200 and response.text.startswith("http"):
            return response.text.strip()
    except Exception as e:
        print(f"Upload error: {e}")
    
    return None

async def background_download(video_id: str, title: str, duration: str):
    """Process video in background"""
    try:
        # Download
        file_path = download_video_simple(video_id)
        if not file_path:
            return
        
        # Upload
        catbox_url = upload_to_catbox(file_path)
        if not catbox_url:
            return
        
        # Clean up
        try:
            os.remove(file_path)
        except:
            pass
        
        # Save to DB
        await videos_col.update_one(
            {"video_id": video_id},
            {"$set": {
                "video_id": video_id,
                "title": title,
                "duration": duration,
                "catbox_link": catbox_url,
                "cached_at": datetime.datetime.now()
            }},
            upsert=True
        )
        
        # Update RAM cache
        RAM_CACHE[video_id] = {
            "status": 200,
            "title": title,
            "duration": duration,
            "link": catbox_url,
            "video_id": video_id,
            "cached": True
        }
        
        print(f"✅ Background processed: {video_id}")
    except Exception as e:
        print(f"❌ Background error: {e}")

# ─────────────────────────────
# SINGLE MAIN ENDPOINT
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    """
    ⚡ SINGLE ENDPOINT - ULTRA FAST
    Query can be: video ID, YouTube URL, or search term
    """
    
    start_time = time.time()
    
    # 1. VERIFY API KEY
    key_valid, key_error = await verify_key_fast(key)
    if not key_valid:
        return {
            "status": 403,
            "title": None,
            "duration": None,
            "link": None,
            "video_id": None,
            "error": key_error
        }
    
    # 2. EXTRACT OR SEARCH FOR VIDEO ID
    video_id = extract_video_id(query)
    title = None
    duration = None
    
    if video_id:
        # Direct video ID or URL
        info = get_video_info(video_id)
        if info:
            title = info["title"]
            duration = info["duration"]
        else:
            title = f"Video {video_id}"
            duration = "unknown"
    else:
        # Search by query
        search_result = quick_search(query)
        if not search_result:
            return {
                "status": 404,
                "title": None,
                "duration": None,
                "link": None,
                "video_id": None,
                "error": "Video not found"
            }
        
        video_id = search_result["id"]
        title = search_result["title"]
        duration = search_result["duration"]
    
    # 3. ⚡⚡⚡ RAM CACHE CHECK (INSTANT - 1ms)
    if video_id in RAM_CACHE:
        response = RAM_CACHE[video_id].copy()
        response["response_time_ms"] = int((time.time() - start_time) * 1000)
        return response
    
    # 4. ⚡ DB CACHE CHECK (FAST - ~50ms)
    try:
        cached = await videos_col.find_one({"video_id": video_id})
        if cached and cached.get("catbox_link"):
            response = {
                "status": 200,
                "title": cached["title"],
                "duration": cached.get("duration", "unknown"),
                "link": cached["catbox_link"],
                "video_id": video_id,
                "cached": True
            }
            RAM_CACHE[video_id] = response
            response["response_time_ms"] = int((time.time() - start_time) * 1000)
            return response
    except Exception as e:
        print(f"DB cache error: {e}")
    
    # 5. NEW VIDEO - START BACKGROUND PROCESS
    asyncio.create_task(background_download(video_id, title, duration))
    
    # Return immediate response
    response_time = int((time.time() - start_time) * 1000)
    
    return {
        "status": 202,
        "title": title,
        "duration": duration,
        "link": None,
        "video_id": video_id,
        "message": "Video is being processed. Try again in 30 seconds.",
        "note": "First request takes 2-3 minutes. Next time: instant!",
        "response_time_ms": response_time
    }

# ─────────────────────────────
# HEALTH CHECK (FOR UPTIME ROBOT)
# ─────────────────────────────
@app.get("/")
async def health_check():
    """Simple health check for uptime monitoring"""
    return {"status": "online", "timestamp": datetime.datetime.now().isoformat()}

# ─────────────────────────────
# STARTUP - PRELOAD CACHE
# ─────────────────────────────
@app.on_event("startup")
async def load_popular_videos():
    """Load popular videos into RAM cache"""
    try:
        # Get recently cached videos
        recent = await videos_col.find().sort("cached_at", -1).limit(50).to_list(None)
        
        for video in recent:
            if video.get("catbox_link"):
                RAM_CACHE[video["video_id"]] = {
                    "status": 200,
                    "title": video["title"],
                    "duration": video.get("duration", "unknown"),
                    "link": video["catbox_link"],
                    "video_id": video["video_id"],
                    "cached": True
                }
        
        print(f"✅ Loaded {len(RAM_CACHE)} videos into RAM cache")
    except Exception as e:
        print(f"⚠️ Cache preload error: {e}")

# ─────────────────────────────
# REQUIREMENTS
# ─────────────────────────────
"""
Add to requirements.txt:
fastapi==0.104.1
uvicorn==0.24.0
motor==3.3.2
yt-dlp==2023.11.16
requests==2.31.0
pymongo==4.5.0
"""
