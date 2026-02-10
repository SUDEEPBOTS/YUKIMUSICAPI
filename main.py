import os
import time
import datetime
import requests
import re
import asyncio
import uuid
import aiohttp
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOGGER_ID = -1003639584506  # Tera Logger ID
CATBOX_UPLOAD = "https://catbox.moe/user/api.php"

# 👇 API URL LOADER
FALLBACK_API_URL = "https://shrutibots.site"
YOUR_API_URL = None

async def load_api_url():
    global YOUR_API_URL
    try:
        async with aiohttp.ClientSession() as session:
            # Pastebin se API URL uthana (Dynamic Switch)
            async with session.get("https://pastebin.com/raw/rLsBhAQa", timeout=5) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    YOUR_API_URL = text.strip()
                else:
                    YOUR_API_URL = FALLBACK_API_URL
    except Exception as e:
        print(f"⚠️ API Load Error: {e}")
        YOUR_API_URL = FALLBACK_API_URL
    print(f"✅ Using External Downloader API: {YOUR_API_URL}")

app = FastAPI(title="⚡ Sudeep API (External Bypass)")

# ─────────────────────────────
# DATABASE
# ─────────────────────────────
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["MusicAPI_DB12"]
videos_col = db["videos_cache"]  # Spelling correct kar di 'cacht' -> 'cache'
keys_col = db["api_users"]
queries_col = db["query_mapping"]

# Startup Event
@app.on_event("startup")
async def startup_event():
    await load_api_url()

# ─────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────
def extract_video_id(q: str):
    if not q: return None
    q = q.strip()
    if len(q) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', q): return q
    patterns = [r'(?:v=|\/)([0-9A-Za-z_-]{11})', r'youtu\.be\/([0-9A-Za-z_-]{11})']
    for pattern in patterns:
        match = re.search(pattern, q)
        if match: return match.group(1)
    return None

def format_time(seconds):
    try: return f"{int(seconds)//60}:{int(seconds)%60:02d}"
    except: return "0:00"

def get_fallback_thumb(vid_id):
    return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

def send_telegram_log(title, duration, link, vid_id):
    if not BOT_TOKEN: return
    try:
        msg = (
            f"🍫 **ɴᴇᴡ sᴏɴɢ (API Bypass)**\n\n"
            f"🫶 **ᴛɪᴛʟᴇ:** {title}\n\n"
            f"⏱ **ᴅᴜʀᴀᴛɪᴏɴ:** {duration}\n"
            f"🛡️ **ɪᴅ:** `{vid_id}`\n"
            f"👀 [ʟɪɴᴋ]({link})\n\n"
            f"🍭 @Kaito_3_2"
        )
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": LOGGER_ID, "text": msg, "parse_mode": "Markdown"}
        )
    except Exception as e:
        print(f"❌ Logger Error: {e}")

# 🔥 STEP 1: METADATA (Sync function, will run in thread)
def get_video_id_and_meta_sync(query: str):
    ydl_opts = {
        'quiet': True, 'skip_download': True, 'extract_flat': True, 'noplaylist': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}}
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            direct_id = extract_video_id(query)
            if direct_id:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={direct_id}", download=False)
                thumb = info.get('thumbnail') or get_fallback_thumb(direct_id)
                return direct_id, info.get('title'), format_time(info.get('duration')), thumb
            else:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if info and 'entries' in info and info['entries']:
                    v = info['entries'][0]
                    vid_id = v['id']
                    thumb = v.get('thumbnail') or get_fallback_thumb(vid_id)
                    return vid_id, v['title'], format_time(v.get('duration')), thumb
    except Exception as e:
        print(f"Search Error: {e}")
    return None, None, None, None

def upload_catbox_sync(path: str):
    if not os.path.exists(path): return None
    try:
        with open(path, "rb") as f:
            r = requests.post(CATBOX_UPLOAD, data={"reqtype": "fileupload"}, files={"fileToUpload": f}, timeout=120)
        return r.text.strip() if r.status_code == 200 and r.text.startswith("http") else None
    except: return None

# 🔥 STEP 2: DOWNLOAD VIA EXTERNAL API (Corrected)
async def external_api_download(video_id: str):
    global YOUR_API_URL
    if not YOUR_API_URL: await load_api_url()

    # ID se Full Link banao (External API ko link chahiye hota hai usually)
    full_link = f"https://www.youtube.com/watch?v={video_id}"
    random_name = str(uuid.uuid4())
    out_path = f"downloads/{random_name}.mp4" # Temp folder

    # Ensure directory exists
    os.makedirs("downloads", exist_ok=True)

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Get Token from External API
            params = {"url": full_link, "type": "video"} 
            print(f"🌍 Calling External API: {YOUR_API_URL} for {video_id}")
            
            async with session.get(f"{YOUR_API_URL}/download", params=params, timeout=30) as resp:
                if resp.status != 200:
                    print(f"❌ External API Step 1 Failed: {resp.status}")
                    return None
                data = await resp.json()
                token = data.get("download_token")
                
                if not token:
                    print("❌ No Token Received")
                    return None

            # 2. Stream File (Actual Download)
            # Dhyan de: Stream URL format external API par depend karega
            stream_url = f"{YOUR_API_URL}/stream/{video_id}?type=video" # Verify karna ki ye ID leta hai ya kuch aur
            
            headers = {"X-Download-Token": token}
            
            async with session.get(stream_url, headers=headers, timeout=600) as resp:
                if resp.status != 200:
                    print(f"❌ Stream Failed: {resp.status}")
                    return None
                
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(16384): # 16KB chunks
                        f.write(chunk)
            
            # Verify file size
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1024: # > 1KB
                return out_path
            return None

    except Exception as e:
        print(f"❌ External API Download Error: {e}")
        if os.path.exists(out_path): os.remove(out_path)
        return None

# ─────────────────────────────
# AUTH CHECK
# ─────────────────────────────
async def verify_and_count(key: str):
    doc = await keys_col.find_one({"api_key": key})
    if not doc or not doc.get("active", True): return False, "Invalid Key"
    
    today = str(datetime.date.today())
    # Reset limit if new day
    if doc.get("last_reset") != today:
        await keys_col.update_one({"api_key": key}, {"$set": {"used_today": 0, "last_reset": today}})
        current_usage = 0
    else:
        current_usage = doc.get("used_today", 0)

    if current_usage >= doc.get("daily_limit", 100): return False, "Limit Exceeded"

    # Count badhana
    await keys_col.update_one({"api_key": key}, {"$inc": {"used_today": 1, "total_usage": 1}})
    return True, None

# ─────────────────────────────
# MAIN ROUTE
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    start_time = time.time()
    
    # 1. Auth Check
    is_valid, err = await verify_and_count(key)
    if not is_valid: return {"status": 403, "error": err}

    clean_query = query.strip().lower()
    
    # 2. Check Cache for ID (Taaki baar baar search na karna pade)
    video_id = None
    cached_q = await queries_col.find_one({"query": clean_query})
    
    title, duration, thumbnail = "Unknown", "0:00", None

    if cached_q:
        video_id = cached_q["video_id"]
        # Metadata fetch from DB
        meta = await videos_col.find_one({"video_id": video_id})
        if meta:
            title = meta.get("title", "Unknown")
            duration = meta.get("duration", "0:00")
            thumbnail = meta.get("thumbnail")
    
    # 3. Agar ID nahi mili, to Search karo (Non-Blocking way mein)
    if not video_id:
        video_id, title, duration, thumbnail = await asyncio.to_thread(get_video_id_and_meta_sync, query)
        if video_id:
             await queries_col.update_one({"query": clean_query}, {"$set": {"video_id": video_id}}, upsert=True)

    if not video_id: return {"status": 404, "error": "Not Found on YouTube"}

    # 4. Check Database for Existing Catbox Link
    cached_video = await videos_col.find_one({"video_id": video_id})
    if cached_video and cached_video.get("catbox_link"):
        return {
            "status": 200, 
            "title": cached_video.get("title", title), 
            "duration": cached_video.get("duration", duration),
            "link": cached_video["catbox_link"], 
            "id": video_id, 
            "thumbnail": cached_video.get("thumbnail", thumbnail), 
            "cached": True,
            "response_time": f"{time.time()-start_time:.2f}s"
        }

    # 5. 🔥 DOWNLOAD VIA EXTERNAL API (Bypass)
    # Metadata update kar do pehle
    await videos_col.update_one(
        {"video_id": video_id}, 
        {"$set": {"video_id": video_id, "title": title, "duration": duration, "thumbnail": thumbnail}}, 
        upsert=True
    )

    print(f"⏳ Bypassing Download: {title} [{video_id}]")
    file_path = await external_api_download(video_id)
    
    if not file_path: 
        return {"status": 500, "error": "Download Failed (External API Error)"}

    # 6. Upload to Catbox (Non-Blocking)
    print(f"📤 Uploading to Catbox: {file_path}")
    link = await asyncio.to_thread(upload_catbox_sync, file_path)
    
    # Cleanup Temp File
    if os.path.exists(file_path): os.remove(file_path)

    if not link: return {"status": 500, "error": "Upload to Catbox Failed"}

    # 7. Save Link to DB
    await videos_col.update_one(
        {"video_id": video_id},
        {"$set": {"catbox_link": link, "cached_at": datetime.datetime.now()}}
    )
    
    # Telegram Log
    asyncio.create_task(asyncio.to_thread(send_telegram_log, title, duration, link, video_id))

    return {
        "status": 200, 
        "title": title, 
        "duration": duration, 
        "link": link,
        "id": video_id, 
        "thumbnail": thumbnail, 
        "cached": False,
        "response_time": f"{time.time()-start_time:.2f}s"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    
