import os
import time
import datetime
import subprocess
import requests
import json
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp
import re

# ─────────────────────────────
# ENV CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI", "mongodb://localhost:27017")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CONTACT = "@Kaito_3_2"

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"
COOKIES_PATH = os.getenv("COOKIES_PATH", "/app/cookies.txt")

# ─────────────────────────────
# APP INIT
# ─────────────────────────────
app = FastAPI(title="Sudeep Music API ⚡")

try:
    mongo = AsyncIOMotorClient(MONGO_URL)
    db = mongo["MusicAPI_DB1"]
    videos_col = db["videos_cachet"]
    keys_col = db["api_users"]
    print("✅ MongoDB connected")
except Exception as e:
    print(f"⚠️ MongoDB connection error: {e}")
    videos_col = None
    keys_col = None

# RAM Cache
MEM_CACHE = {}

# ─────────────────────────────
# API KEY VERIFY
# ─────────────────────────────
async def verify_api_key(key: str):
    if not keys_col:
        return True, None  # Development mode
    
    doc = await keys_col.find_one({"api_key": key, "active": True})
    if not doc:
        return False, "Invalid API key"

    now = int(time.time())

    if now > doc["expires_at"]:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": doc["user_id"],
                    "text": (
                        "⚠️ Your API key has expired.\n\n"
                        f"Please contact {ADMIN_CONTACT} to renew."
                    )
                },
                timeout=10
            )
        except:
            pass
        return False, "API key expired"

    today = str(datetime.date.today())
    if doc.get("last_reset") != today:
        await keys_col.update_one(
            {"api_key": key},
            {"$set": {"used_today": 0, "last_reset": today}}
        )
        doc["used_today"] = 0

    if doc["used_today"] >= doc["daily_limit"]:
        return False, "Daily limit exceeded"

    await keys_col.update_one(
        {"api_key": key},
        {"$inc": {"used_today": 1}}
    )

    return True, None

# ─────────────────────────────
# HELPERS (YT-DLP BASED - NO EXTERNAL LIB)
# ─────────────────────────────
def yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"

def extract_video_id(q: str):
    q = q.strip()
    
    # Already a video ID
    if len(q) == 11 and " " not in q and "=" not in q and "/" not in q:
        return q
    
    # Full YouTube URL
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'embed\/([0-9A-Za-z_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, q)
        if match:
            return match.group(1)
    
    return None

def format_duration(seconds: int) -> str:
    """Seconds to MM:SS or HH:MM:SS format"""
    if not seconds:
        return "0:00"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"

def search_youtube_with_ytdlp(query: str):
    """
    yt-dlp का उपयोग करके YouTube search - MOST RELIABLE
    """
    try:
        print(f"🔍 Searching YouTube for: '{query}'")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'default_search': 'ytsearch',
            'extract_flat': True,
            'force_generic_extractor': False,
        }
        
        # Add cookies if available
        if os.path.exists(COOKIES_PATH):
            ydl_opts['cookiefile'] = COOKIES_PATH
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Search for the video
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
            
            if not info or 'entries' not in info or not info['entries']:
                print(f"❌ No search results for: {query}")
                return None
            
            video = info['entries'][0]
            
            result = {
                "id": video.get('id'),
                "title": video.get('title', 'Unknown Title'),
                "duration": format_duration(video.get('duration', 0)),
                "channel": video.get('uploader', 'Unknown Channel'),
                "view_count": video.get('view_count', 0),
                "url": video.get('url', '')
            }
            
            print(f"✅ Found: {result['title']} ({result['id']})")
            return result
            
    except Exception as e:
        print(f"❌ Search error: {e}")
        # Fallback to alternative method
        return search_youtube_fallback(query)

def search_youtube_fallback(query: str):
    """
    Fallback search method using requests
    """
    try:
        import urllib.parse
        search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        
        # Extract video IDs from the page
        video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', response.text)
        
        if video_ids:
            # Get the first unique video ID
            video_id = video_ids[0]
            
            # Try to get video info
            video_info = get_video_info(video_id)
            if video_info:
                return video_info
            
            return {
                "id": video_id,
                "title": query,
                "duration": "unknown",
                "channel": "Unknown",
                "view_count": 0
            }
        
    except Exception as e:
        print(f"❌ Fallback search error: {e}")
    
    return None

def get_video_info(video_id: str):
    """
    Get detailed video info using yt-dlp
    """
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        
        if os.path.exists(COOKIES_PATH):
            ydl_opts['cookiefile'] = COOKIES_PATH
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            return {
                "id": video_id,
                "title": info.get('title', f'Video {video_id}'),
                "duration": format_duration(info.get('duration', 0)),
                "channel": info.get('uploader', 'Unknown Channel'),
                "view_count": info.get('view_count', 0),
            }
            
    except Exception as e:
        print(f"❌ Video info error for {video_id}: {e}")
        return None

def upload_catbox(path: str) -> str:
    try:
        with open(path, "rb") as f:
            r = requests.post(
                CATBOX_UPLOAD,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                timeout=180
            )
        
        if r.status_code == 200 and r.text.startswith("https://"):
            return r.text.strip()
        else:
            print(f"❌ Catbox upload failed: {r.status_code} - {r.text[:100]}")
            raise Exception(f"Catbox upload failed: {r.status_code}")
            
    except Exception as e:
        print(f"❌ Upload error: {e}")
        raise Exception(f"Upload failed: {str(e)}")

def auto_download_video(video_id: str) -> str:
    """
    Download video using yt-dlp
    """
    print(f"⬇️ Starting download for: {video_id}")
    
    # Check if cookies exist
    cookies_available = os.path.exists(COOKIES_PATH)
    print(f"🍪 Cookies available: {cookies_available}")
    
    out_template = f"/tmp/{video_id}.%(ext)s"
    
    # Build yt-dlp command
    cmd = [
        "python", "-m", "yt_dlp",
        "--no-playlist",
        "--geo-bypass",
        "--force-ipv4",
        "-f", "best[height<=720]/best",
        "--merge-output-format", "mp4",
        "--output", out_template,
        f"https://www.youtube.com/watch?v={video_id}"
    ]
    
    # Add cookies if available
    if cookies_available:
        cmd.extend(["--cookies", COOKIES_PATH])
    
    print(f"🔄 Running command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            timeout=900,
            capture_output=True,
            text=True
        )
        
        print(f"✅ Download stdout: {result.stdout[:200]}")
        if result.stderr:
            print(f"⚠️ Download stderr: {result.stderr[:200]}")
        
        # Find the actual output file
        expected_file = f"/tmp/{video_id}.mp4"
        if os.path.exists(expected_file):
            return expected_file
            
        # Try to find any file with the video_id in the name
        import glob
        files = glob.glob(f"/tmp/{video_id}.*")
        if files:
            return files[0]
            
        raise Exception("Downloaded file not found")
        
    except subprocess.TimeoutExpired:
        raise Exception("Download timeout (15 minutes)")
    except subprocess.CalledProcessError as e:
        print(f"❌ Download failed: {e.stderr}")
        raise Exception(f"Download failed: {e.stderr[:100]}")
    except Exception as e:
        print(f"❌ Download error: {e}")
        raise

# ─────────────────────────────
# MAIN API ENDPOINTS
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str | None = None, skip_cache: bool = False):
    print(f"\n" + "="*50)
    print(f"📥 Request received - Query: '{query}', Key: {key}")
    
    # Skip key verification in development
    # if not key:
    #     return {"status": 401, "error": "API key required"}
    
    # ok, err = await verify_api_key(key)
    # if not ok:
    #     return {"status": 403, "error": err}
    
    video_id = extract_video_id(query)
    print(f"🎯 Extracted video_id: {video_id}")
    
    # 🔍 Search if needed
    if not video_id:
        print(f"🔍 Performing search for query: '{query}'")
        search_result = search_youtube_with_ytdlp(query)
        
        if not search_result:
            print(f"❌ No results found for: '{query}'")
            return {
                "status": 404,
                "error": f"No video found for: {query}",
                "title": None,
                "duration": None,
                "link": None,
                "video_id": None
            }
        
        video_id = search_result["id"]
        title = search_result["title"]
        duration = search_result["duration"]
        print(f"✅ Search successful: {title}")
        
    else:
        # Direct video ID provided
        print(f"🎬 Direct video ID: {video_id}")
        video_info = get_video_info(video_id)
        
        if video_info:
            title = video_info["title"]
            duration = video_info["duration"]
        else:
            title = f"Video {video_id}"
            duration = "unknown"
            print(f"⚠️ Could not fetch video info, using default title")
    
    # ⚡ RAM Cache (skip if requested)
    if not skip_cache and video_id in MEM_CACHE:
        print(f"⚡ Serving from RAM cache: {video_id}")
        return MEM_CACHE[video_id]
    
    # 💾 DB Cache (skip if requested)
    if not skip_cache and videos_col:
        cached = await videos_col.find_one({"video_id": video_id})
        if cached:
            print(f"💾 Serving from DB cache: {video_id}")
            resp = {
                "status": 200,
                "title": cached["title"],
                "duration": cached.get("duration", "unknown"),
                "link": cached["catbox_link"],
                "video_id": video_id,
                "cached": True
            }
            MEM_CACHE[video_id] = resp
            return resp
    
    # ⬇️ Download → Catbox
    try:
        print(f"⬇️ Starting download process for: {video_id}")
        
        # Download video
        file_path = auto_download_video(video_id)
        print(f"✅ Downloaded: {file_path} ({os.path.getsize(file_path)//1024} KB)")
        
        # Upload to Catbox
        print(f"📤 Uploading to Catbox...")
        try:
            catbox_link = upload_catbox(file_path)
            print(f"✅ Uploaded to: {catbox_link}")
        except Exception as upload_error:
            print(f"❌ Catbox upload failed: {upload_error}")
            raise upload_error
        
        # Cleanup
        try:
            os.remove(file_path)
            print(f"🗑️ Cleaned temp file")
        except:
            pass
        
        # Update video info if needed
        if title == f"Video {video_id}" or title == "Unknown Title":
            video_info = get_video_info(video_id)
            if video_info:
                title = video_info["title"]
                duration = video_info["duration"]
                print(f"📝 Updated title: {title}")
        
        # Save to DB
        if videos_col:
            try:
                await videos_col.update_one(
                    {"video_id": video_id},
                    {"$set": {
                        "video_id": video_id,
                        "title": title,
                        "duration": duration,
                        "catbox_link": catbox_link,
                        "cached_at": datetime.datetime.utcnow(),
                        "query_used": query
                    }},
                    upsert=True
                )
                print(f"💾 Saved to database")
            except Exception as db_error:
                print(f"⚠️ Database save error: {db_error}")
        
        # Prepare response
        resp = {
            "status": 200,
            "title": title,
            "duration": duration,
            "link": catbox_link,
            "video_id": video_id,
            "cached": False,
            "query": query
        }
        
        # Add to RAM cache
        MEM_CACHE[video_id] = resp
        
        print(f"✅ Request completed successfully!")
        print("="*50)
        
        return resp
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print("="*50)
        
        return {
            "status": 500,
            "error": str(e),
            "title": title if 'title' in locals() else None,
            "duration": duration if 'duration' in locals() else None,
            "link": None,
            "video_id": video_id,
            "query": query
        }

# ─────────────────────────────
# TEST & DEBUG ENDPOINTS
# ─────────────────────────────
@app.get("/test")
async def test_endpoint():
    """Simple test endpoint"""
    return {
        "status": "active",
        "service": "Sudeep Music API",
        "timestamp": datetime.datetime.now().isoformat(),
        "endpoints": {
            "/getvideo": "Main endpoint",
            "/search": "Test search only",
            "/info/{video_id}": "Get video info",
            "/health": "Health check"
        }
    }

@app.get("/search")
async def search_only(query: str):
    """Search only, no download"""
    print(f"🔍 Search request: {query}")
    
    video_id = extract_video_id(query)
    
    if video_id:
        info = get_video_info(video_id)
        if info:
            return {
                "status": 200,
                "type": "video_id",
                "result": info
            }
    
    # Search by query
    result = search_youtube_with_ytdlp(query)
    if result:
        return {
            "status": 200,
            "type": "search",
            "result": result
        }
    
    return {
        "status": 404,
        "error": "No results found"
    }

@app.get("/info/{video_id}")
async def get_video_info_endpoint(video_id: str):
    """Get video information only"""
    info = get_video_info(video_id)
    if info:
        return {
            "status": 200,
            "info": info
        }
    
    return {
        "status": 404,
        "error": "Video not found"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.datetime.now().isoformat(),
        "service": "Sudeep Music API",
        "version": "2.0"
    }

# ─────────────────────────────
# STARTUP
# ─────────────────────────────
@app.on_event("startup")
async def startup_event():
    print("\n" + "="*50)
    print("🚀 Sudeep Music API Starting...")
    print(f"📁 Cookies path: {COOKIES_PATH}")
    print(f"📦 Cookies exists: {os.path.exists(COOKIES_PATH)}")
    print("="*50 + "\n")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
