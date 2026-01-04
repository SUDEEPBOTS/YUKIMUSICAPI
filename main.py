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
import json

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CONTACT = "@Kaito_3_2"
CATBOX_UPLOAD = "https://catbox.moe/user/api.php"

# COOKIES PATH - CHECK MULTIPLE LOCATIONS
COOKIES_PATHS = [
    "/app/cookies.txt",      # Render default
    "./cookies.txt",         # Current directory
    "/etc/cookies.txt",      # System directory
    "/tmp/cookies.txt"       # Temp directory
]

# Find cookies file
COOKIES_PATH = None
for path in COOKIES_PATHS:
    if os.path.exists(path):
        COOKIES_PATH = path
        print(f"✅ Found cookies at: {path}")
        break

if not COOKIES_PATH:
    print("⚠️ WARNING: No cookies.txt found! YouTube may block downloads.")

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
# CORE FUNCTIONS
# ─────────────────────────────
def extract_video_id(q: str):
    """Extract video ID from any input"""
    if not q:
        return None
    
    q = q.strip()
    
    # Direct video ID
    if len(q) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', q):
        return q
    
    # URL patterns
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11})',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, q)
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
    """Search with cookies support"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': True,
        }
        
        # Add cookies if available
        if COOKIES_PATH and os.path.exists(COOKIES_PATH):
            ydl_opts['cookiefile'] = COOKIES_PATH
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
    """Get video info with cookies"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        
        # Add cookies if available
        if COOKIES_PATH and os.path.exists(COOKIES_PATH):
            ydl_opts['cookiefile'] = COOKIES_PATH
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            return {
                "id": video_id,
                "title": info.get('title', f'Video {video_id}'),
                "duration": format_time(info.get('duration'))
            }
    except Exception as e:
        print(f"Video info error: {e}")
        return None

async def verify_key_fast(key: str):
    """API key verification"""
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

# ─────────────────────────────
# 🔥 UPDATED DOWNLOAD FUNCTION (FIXES CORRUPTION)
# ─────────────────────────────
def download_video_with_cookies(video_id: str):
    """Download with FFmpeg merge to fix corrupted videos"""
    try:
        out_file = f"/tmp/{video_id}.mp4"
        
        # Clean existing file
        if os.path.exists(out_file):
            os.remove(out_file)
        
        # Command setup with FFmpeg merge and Recode
        cmd = [
            "yt-dlp",
            # Try to get Best Video (MP4) + Best Audio (M4A) OR fallback to Best Single
            "-f", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[height<=480]",
            
            # Force merge into MP4 container
            "--merge-output-format", "mp4",
            
            # Ensure codec is compatible (Fixes 'unsupported format' on phones)
            "--recode-video", "mp4",
            
            "--no-playlist",
            "--socket-timeout", "30",
            
            # Output file
            "-o", out_file,
            f"https://www.youtube.com/watch?v={video_id}"
        ]
        
        # Add cookies if available
        if COOKIES_PATH and os.path.exists(COOKIES_PATH):
            cmd.extend(["--cookies", COOKIES_PATH])
            print("🍪 Using cookies for download")
        
        print(f"Running command: {' '.join(cmd)}")
        
        # Run download (Increased timeout to 10 mins for merging)
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=600
        )
        
        if result.returncode != 0:
            print(f"❌ Download error: {result.stderr}")
            # Fallback for tough videos (Single file only)
            print("🔄 Retrying with fallback mode...")
            fallback_cmd = [
                "yt-dlp",
                "-f", "best[height<=480]",
                "-o", out_file,
                f"https://www.youtube.com/watch?v={video_id}"
            ]
            subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=300)
        
        # Verify file
        if os.path.exists(out_file):
            file_size = os.path.getsize(out_file)
            if file_size < 1024: # Less than 1KB = Corrupted
                print("❌ File too small (Corrupted)")
                return None
            
            print(f"✅ Downloaded: {out_file} ({file_size} bytes)")
            return out_file
        else:
            print(f"❌ File not created: {out_file}")
            return None
            
    except subprocess.TimeoutExpired:
        print("⏰ Download timeout")
        return None
    except Exception as e:
        print(f"Download exception: {e}")
        return None

def upload_to_catbox(file_path: str):
    """Upload to catbox with reqtype fix"""
    try:
        print(f"📤 Uploading: {file_path}")
        
        with open(file_path, "rb") as f:
            response = requests.post(
                CATBOX_UPLOAD,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                timeout=120
            )
        
        if response.status_code == 200 and response.text.startswith("http"):
            print(f"✅ Upload success: {response.text.strip()}")
            return response.text.strip()
        else:
            print(f"❌ Upload failed: {response.text}")
            return None
            
    except Exception as e:
        print(f"Upload error: {e}")
        return None

async def background_download(video_id: str, title: str, duration: str):
    """Process video in background with Size Fix"""
    try:
        print(f"🔄 Starting background download for: {video_id}")
        
        # 1. Download
        file_path = download_video_with_cookies(video_id)
        if not file_path:
            return
        
        # Get Size BEFORE upload/delete
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        # 2. Upload
        catbox_url = upload_to_catbox(file_path)
        if not catbox_url:
            try:
                os.remove(file_path)
            except:
                pass
            return
        
        # 3. Clean up
        try:
            os.remove(file_path)
            print(f"🧹 Cleaned up temp file")
        except:
            pass
        
        # 4. Save to DB
        await videos_col.update_one(
            {"video_id": video_id},
            {"$set": {
                "video_id": video_id,
                "title": title,
                "duration": duration,
                "catbox_link": catbox_url,
                "cached_at": datetime.datetime.now(),
                "size_mb": file_size_mb
            }},
            upsert=True
        )
        
        # 5. Update RAM cache
        RAM_CACHE[video_id] = {
            "status": 200,
            "title": title,
            "duration": duration,
            "link": catbox_url,
            "video_id": video_id,
            "cached": True
        }
        
        print(f"✅ COMPLETED: {title}")
        
    except Exception as e:
        print(f"❌ Background process error: {e}")

# ─────────────────────────────
# SINGLE MAIN ENDPOINT
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    """
    ⚡ SINGLE ENDPOINT
    Query: video ID, URL, or search term
    """
    
    start_time = time.time()
    
    # 1. VERIFY API KEY
    key_valid, key_error = await verify_key_fast(key)
    if not key_valid:
        return {
            "status": 403,
            "error": key_error,
            "response_time_ms": int((time.time() - start_time) * 1000)
        }
    
    print(f"📥 Request: '{query}'")
    
    # 2. EXTRACT OR SEARCH FOR VIDEO ID
    video_id = extract_video_id(query)
    title = None
    duration = None
    
    if video_id:
        # Direct video ID or URL
        print(f"🎬 Video ID: {video_id}")
        info = get_video_info(video_id)
        if info:
            title = info["title"]
            duration = info["duration"]
            print(f"✅ Video info: {title}")
        else:
            title = f"Video {video_id}"
            duration = "unknown"
    else:
        # Search by query
        print(f"🔍 Searching: {query}")
        search_result = quick_search(query)
        if not search_result:
            return {
                "status": 404,
                "error": "Video not found",
                "response_time_ms": int((time.time() - start_time) * 1000)
            }
        
        video_id = search_result["id"]
        title = search_result["title"]
        duration = search_result["duration"]
        print(f"✅ Found: {title}")
    
    # 3. ⚡ RAM CACHE CHECK (INSTANT)
    if video_id in RAM_CACHE:
        response = RAM_CACHE[video_id].copy()
        response["response_time_ms"] = int((time.time() - start_time) * 1000)
        print(f"⚡ Served from RAM cache")
        return response
    
    # 4. ⚡ DB CACHE CHECK
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
            print(f"💾 Served from DB cache")
            return response
    except Exception as e:
        print(f"DB error: {e}")
    
    # 5. NEW VIDEO - START BACKGROUND PROCESS
    print(f"🔄 Starting background process for new video")
    asyncio.create_task(background_download(video_id, title, duration))
    
    # Return immediate response
    response_time = int((time.time() - start_time) * 1000)
    
    return {
        "status": 202,
        "title": title,
        "duration": duration,
        "video_id": video_id,
        "message": "Video is being processed. Try again in 60 seconds.",
        "note": "First time may take 3-5 minutes. Next time will be instant!",
        "cookies_status": "available" if COOKIES_PATH else "missing",
        "response_time_ms": response_time
    }

# ─────────────────────────────
# ADDITIONAL HELPFUL ENDPOINTS
# ─────────────────────────────
@app.get("/")
async def root():
    """Root endpoint with info"""
    return {
        "status": "online",
        "service": "Sudeep Music API",
        "endpoint": "/getvideo?query=...&key=...",
        "cookies": "available" if COOKIES_PATH else "missing"
    }

@app.get("/check_cookies")
async def check_cookies():
    """Check if cookies.txt is working"""
    if not COOKIES_PATH:
        return {"status": "error", "message": "cookies.txt not found"}
    
    if os.path.exists(COOKIES_PATH):
        file_size = os.path.getsize(COOKIES_PATH)
        return {
            "status": "success",
            "path": COOKIES_PATH,
            "size_bytes": file_size,
            "exists": True
        }
    
    return {"status": "error", "message": "File not found"}

@app.get("/cache_stats")
async def cache_stats():
    """Get cache statistics"""
    return {
        "ram_cache_size": len(RAM_CACHE),
        "cookies_available": COOKIES_PATH is not None
    }

# ─────────────────────────────
# STARTUP
# ─────────────────────────────
@app.on_event("startup")
async def startup_tasks():
    """Run on startup"""
    print("\n" + "="*50)
    print("🚀 Sudeep Music API Starting...")
    
    # Check cookies
    if COOKIES_PATH:
        print(f"✅ Cookies found: {COOKIES_PATH}")
        try:
            size = os.path.getsize(COOKIES_PATH)
            print(f"📦 Cookies size: {size} bytes")
        except:
            print("⚠️ Could not read cookies file")
    else:
        print("⚠️ WARNING: No cookies.txt found!")
        print("📝 YouTube may block downloads without cookies")
    
    # Load cache from DB
    try:
        recent = await videos_col.find().sort("cached_at", -1).limit(100).to_list(None)
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
        print(f"⚠️ Cache load error: {e}")
    
    print("="*50 + "\n")

# ─────────────────────────────
# RUN APP
# ─────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    
