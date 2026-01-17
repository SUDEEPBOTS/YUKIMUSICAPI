import os
import time
import datetime
import requests
import re
import asyncio
import uuid
import aiohttp  # ⚠️ Ye naya install karna padega: pip install aiohttp
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOGGER_ID = -1003639584506 # Tera Logger ID
CATBOX_UPLOAD = "https://catbox.moe/user/api.php"

# 👇 API URL LOADER
FALLBACK_API_URL = "https://shrutibots.site"
YOUR_API_URL = None

async def load_api_url():
    global YOUR_API_URL
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://pastebin.com/raw/rLsBhAQa", timeout=5) as resp:
                if resp.status == 200:
                    YOUR_API_URL = (await resp.text()).strip()
                else:
                    YOUR_API_URL = FALLBACK_API_URL
    except:
        YOUR_API_URL = FALLBACK_API_URL
    print(f"✅ Using API: {YOUR_API_URL}")

app = FastAPI(title="⚡ Sudeep API (External Bypass)")

# ─────────────────────────────
# DATABASE
# ─────────────────────────────
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["MusicAPI_DB12"]
videos_col = db["videos_cacht"]
keys_col = db["api_users"]
queries_col = db["query_mapping"]

# Startup Event to Load API URL
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

# 🔥 STEP 1: METADATA (Search abhi bhi yt-dlp se lenge, safe hai)
def get_video_id_only(query: str):
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

def upload_catbox(path: str):
    try:
        with open(path, "rb") as f:
            r = requests.post(CATBOX_UPLOAD, data={"reqtype": "fileupload"}, files={"fileToUpload": f}, timeout=120)
        return r.text.strip() if r.status_code == 200 and r.text.startswith("http") else None
    except: return None

# 🔥 STEP 2: DOWNLOAD VIA EXTERNAL API (The Fix)
async def external_api_download(video_id: str):
    global YOUR_API_URL
    if not YOUR_API_URL: await load_api_url()

    random_name = str(uuid.uuid4())
    out_path = f"/tmp/{random_name}.mp4"

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Get Token
            params = {"url": video_id, "type": "video"} # Audio chahiye to "audio" kar dena
            print(f"🌍 Requesting API: {YOUR_API_URL} for {video_id}")
            
            async with session.get(f"{YOUR_API_URL}/download", params=params, timeout=30) as resp:
                if resp.status != 200:
                    print("❌ API Download Step 1 Failed")
                    return None
                data = await resp.json()
                token = data.get("download_token")
                if not token: return None

            # 2. Stream File
            stream_url = f"{YOUR_API_URL}/stream/{video_id}?type=video"
            async with session.get(stream_url, headers={"X-Download-Token": token}, timeout=600) as resp:
                if resp.status != 200: return None
                
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(16384):
                        f.write(chunk)
            
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                return out_path
            return None

    except Exception as e:
        print(f"❌ External API Error: {e}")
        return None

# ─────────────────────────────
# AUTH CHECK
# ─────────────────────────────
async def verify_and_count(key: str):
    doc = await keys_col.find_one({"api_key": key})
    if not doc or not doc.get("active", True): return False, "Invalid Key"
    
    today = str(datetime.date.today())
    if doc.get("last_reset") != today:
        await keys_col.update_one({"api_key": key}, {"$set": {"used_today": 0, "last_reset": today}})
        doc["used_today"] = 0 

    if doc.get("used_today", 0) >= doc.get("daily_limit", 100): return False, "Limit Exceeded"

    await keys_col.update_one({"api_key": key}, {"$inc": {"used_today": 1, "total_usage": 1}})
    return True, None

# ─────────────────────────────
# MAIN ROUTE
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    start_time = time.time()
    is_valid, err = await verify_and_count(key)
    if not is_valid: return {"status": 403, "error": err}

    clean_query = query.strip().lower()
    
    # Check Cache for ID
    video_id = None
    cached_q = await queries_col.find_one({"query": clean_query})
    
    title, duration, thumbnail = "Unknown", "0:00", None

    if cached_q:
        video_id = cached_q["video_id"]
        meta = await videos_col.find_one({"video_id": video_id})
        if meta:
            title = meta.get("title")
            duration = meta.get("duration")
            thumbnail = meta.get("thumbnail")
    
    if not video_id:
        video_id, title, duration, thumbnail = await asyncio.to_thread(get_video_id_only, query)
        if video_id:
             await queries_col.update_one({"query": clean_query}, {"$set": {"video_id": video_id}}, upsert=True)

    if not video_id: return {"status": 404, "error": "Not Found"}

    # DB Check for Link
    cached = await videos_col.find_one({"video_id": video_id})
    if cached and cached.get("catbox_link"):
        return {
            "status": 200, "title": cached.get("title", title), "duration": cached.get("duration", duration),
            "link": cached["catbox_link"], "id": video_id, "thumbnail": cached.get("thumbnail", thumbnail), "cached": True
        }

    # 🔥 NEW DOWNLOAD METHOD
    print(f"⏳ Downloading via API: {title}")
    
    # Metadata save karo taaki next time kaam aaye
    await videos_col.update_one(
        {"video_id": video_id}, 
        {"$set": {"video_id": video_id, "title": title, "duration": duration, "thumbnail": thumbnail}}, 
        upsert=True
    )

    # EXTERNAL API CALL
    file_path = await external_api_download(video_id)
    
    if not file_path: return {"status": 500, "error": "External API Failed"}

    # Upload to Catbox
    link = await asyncio.to_thread(upload_catbox, file_path)
    if os.path.exists(file_path): os.remove(file_path)

    if not link: return {"status": 500, "error": "Upload Failed"}

    await videos_col.update_one(
        {"video_id": video_id},
        {"$set": {"catbox_link": link, "cached_at": datetime.datetime.now()}}
    )
    
    asyncio.create_task(asyncio.to_thread(send_telegram_log, title, duration, link, video_id))

    return {
        "status": 200, "title": title, "duration": duration, "link": link,
        "id": video_id, "thumbnail": thumbnail, "cached": False,
        "response_time": f"{time.time()-start_time:.2f}s"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
            
