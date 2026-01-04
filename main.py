import os
import time
import datetime
import subprocess
import requests
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from youtubesearchpython import VideosSearch
import yt_dlp

# ─────────────────────────────
# ENV CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")          # DM notify ke liye
ADMIN_CONTACT = "@Kaito_3_2"

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"
COOKIES_PATH = "/app/cookies.txt"           # Docker / Render path

# ─────────────────────────────
# APP INIT
# ─────────────────────────────
app = FastAPI(title="Sudeep Music API ⚡")

mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["MusicAPI_DB1"]

videos_col = db["videos_cachet"]
keys_col = db["api_users"]

# RAM Cache
MEM_CACHE = {}

# ─────────────────────────────
# API KEY VERIFY
# ─────────────────────────────
async def verify_api_key(key: str):
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
# HELPERS
# ─────────────────────────────
def yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"

def extract_video_id(q: str):
    q = q.strip()
    if len(q) == 11 and " " not in q and "=" not in q and "/" not in q:
        return q
    if "v=" in q:
        return q.split("v=")[1].split("&")[0]
    if "youtu.be/" in q:
        return q.split("youtu.be/")[1].split("?")[0]
    return None

def format_duration(seconds: int) -> str:
    """Seconds to MM:SS or HH:MM:SS format"""
    if not seconds:
        return "0:00"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"

def search_youtube(query: str):
    """
    YouTube search - only used for initial search
    Returns: {id, title}
    """
    try:
        print(f"🔍 Searching YouTube for: {query}")
        s = VideosSearch(query, limit=1)
        result = s.result()
        
        if not result or "result" not in result or not result["result"]:
            return None
        
        video_data = result["result"][0]
        
        return {
            "id": video_data.get("id"),
            "title": video_data.get("title", "Unknown Title")
        }
    except Exception as e:
        print(f"❌ Search error: {e}")
        return None

def get_video_info_with_ytdlp(video_id: str):
    """yt-dlp का उपयोग करके video की details प्राप्त करें"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'cookiefile': COOKIES_PATH if os.path.exists(COOKIES_PATH) else None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            duration_str = format_duration(info.get('duration', 0))
            
            return {
                "id": video_id,
                "title": info.get('title', 'Unknown Title'),
                "duration": duration_str,
                "channel": info.get('uploader', 'Unknown Channel'),
                "view_count": info.get('view_count', 0),
            }
    except Exception as e:
        print(f"❌ yt-dlp info error: {e}")
        return None

def upload_catbox(path: str) -> str:
    with open(path, "rb") as f:
        r = requests.post(
            CATBOX_UPLOAD,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            timeout=180
        )
    if r.text.startswith("https://"):
        return r.text.strip()
    raise Exception("Catbox upload failed")

def auto_download_video(video_id: str) -> str:
    if not os.path.exists(COOKIES_PATH):
        raise Exception("cookies.txt missing")

    out = f"/tmp/{video_id}.mp4"

    cmd = [
        "python", "-m", "yt_dlp",
        "--cookies", COOKIES_PATH,
        "--js-runtimes", "node",
        "--no-playlist",
        "--geo-bypass",
        "--force-ipv4",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        yt_url(video_id),
        "-o", out
    ]

    subprocess.run(cmd, check=True, timeout=900)
    return out

# ─────────────────────────────
# MAIN API
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str | None = None):
    if not key:
        return {"status": 401, "error": "API key required"}

    ok, err = await verify_api_key(key)
    if not ok:
        return {"status": 403, "error": err}

    video_id = extract_video_id(query)
    title = None
    duration = None

    print(f"🎯 Input query: '{query}' -> Extracted video_id: '{video_id}'")

    # 🔎 Search if needed (name से search)
    if not video_id:
        print(f"🔍 Performing name search for: '{query}'")
        search_data = search_youtube(query)
        if not search_data or not search_data.get("id"):
            return {
                "status": 404,
                "error": "Video not found",
                "title": None,
                "duration": None,
                "link": None,
                "video_id": None
            }

        video_id = search_data["id"]
        title = search_data["title"]
        print(f"✅ Search successful - ID: {video_id}, Title: {title}")
        
        # अब yt-dlp से full details लें
        video_info = get_video_info_with_ytdlp(video_id)
        if video_info:
            title = video_info["title"]
            duration = video_info["duration"]
        else:
            duration = "unknown"
    else:
        # Video ID directly दिया गया है
        print(f"🎬 Direct video ID: {video_id}")
        video_info = get_video_info_with_ytdlp(video_id)
        if video_info:
            title = video_info["title"]
            duration = video_info["duration"]
        else:
            # Fallback
            search_data = search_youtube(video_id)
            if search_data:
                title = search_data["title"]
            else:
                title = f"Video {video_id}"
            duration = "unknown"

    # ⚡ RAM Cache
    if video_id in MEM_CACHE:
        print(f"⚡ Serving from RAM cache: {video_id}")
        return MEM_CACHE[video_id]

    # 💾 DB Cache
    cached = await videos_col.find_one({"video_id": video_id})
    if cached:
        resp = {
            "status": 200,
            "title": cached["title"],
            "duration": cached.get("duration", "unknown"),
            "link": cached["catbox_link"],
            "video_id": video_id,
            "cached": True
        }
        MEM_CACHE[video_id] = resp
        print(f"💾 Serving from DB cache: {video_id}")
        return resp

    # ⬇️ Download → Catbox
    try:
        print(f"⬇️ Downloading video: {video_id}")
        file_path = auto_download_video(video_id)
        print(f"✅ Downloaded to: {file_path}")
        
        print(f"📤 Uploading to Catbox...")
        catbox = upload_catbox(file_path)
        print(f"✅ Uploaded: {catbox}")

        try:
            os.remove(file_path)
            print(f"🗑️ Cleaned temp file: {file_path}")
        except:
            pass

        # अगर title और duration अभी तक नहीं मिले, तो फिर से कोशिश करें
        if not title or title == "Unknown Title":
            video_info = get_video_info_with_ytdlp(video_id)
            if video_info:
                title = video_info["title"]
                duration = video_info["duration"]

        await videos_col.update_one(
            {"video_id": video_id},
            {"$set": {
                "video_id": video_id,
                "title": title or f"Video {video_id}",
                "duration": duration or "unknown",
                "catbox_link": catbox,
                "cached_at": datetime.datetime.utcnow()
            }},
            upsert=True
        )

        resp = {
            "status": 200,
            "title": title or f"Video {video_id}",
            "duration": duration or "unknown",
            "link": catbox,
            "video_id": video_id,
            "cached": False
        }

        MEM_CACHE[video_id] = resp
        return resp

    except subprocess.TimeoutExpired:
        return {
            "status": 408,
            "error": "Download timeout (15 minutes)",
            "title": title,
            "duration": duration,
            "link": None,
            "video_id": video_id
        }
    except Exception as e:
        print(f"❌ Error in get_video: {e}")
        return {
            "status": 500,
            "error": str(e),
            "title": title,
            "duration": duration,
            "link": None,
            "video_id": video_id
        }

# Test endpoint for search
@app.get("/test_search")
async def test_search(query: str):
    """Test search function"""
    # Method 1: VideosSearch
    try:
        s = VideosSearch(query, limit=1)
        result = s.result()
        videosearch_result = result.get("result")[0] if result.get("result") else None
    except Exception as e:
        videosearch_result = f"Error: {e}"
    
    # Method 2: yt-dlp
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # First search
            search_info = ydl.extract_info(f"ytsearch:{query}", download=False)
            ytdlp_search_result = search_info['entries'][0] if search_info['entries'] else None
    except Exception as e:
        ytdlp_search_result = f"Error: {e}"
    
    return {
        "query": query,
        "VideosSearch_result": videosearch_result,
        "ytdlp_search_result": ytdlp_search_result
    }

# Simple endpoint for direct testing
@app.get("/simple_search")
async def simple_search(query: str, key: str):
    """Simple search endpoint for testing"""
    if key != "test123":
        return {"error": "Invalid test key"}
    
    # Use yt-dlp for reliable search
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'cookiefile': COOKIES_PATH if os.path.exists(COOKIES_PATH) else None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Search for video
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            
            if not info or 'entries' not in info or not info['entries']:
                return {"error": "No results found"}
            
            video = info['entries'][0]
            
            return {
                "success": True,
                "video_id": video.get('id'),
                "title": video.get('title'),
                "duration": format_duration(video.get('duration', 0)),
                "url": f"https://youtube.com/watch?v={video.get('id')}"
            }
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────
# HEALTH CHECK
# ─────────────────────────────
@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {
        "status": 200, 
        "message": "Sudeep Music API",
        "endpoints": {
            "/getvideo?query=...&key=...": "Main endpoint",
            "/test_search?query=...": "Test search",
            "/simple_search?query=...&key=test123": "Simple test"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
