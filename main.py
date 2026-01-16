import os
import time
import datetime
import subprocess
import requests
import re
import asyncio
import uuid
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")  # ⚠️ Ye Zaroor Daalna Env Vars mein
LOGGER_ID = -1003639584506          # ✅ Tera Logger Group ID

if not MONGO_URL:
    print("⚠️ MONGO_DB_URI not found.")

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"

# COOKIES PATH CHECK
COOKIES_PATHS = ["/app/cookies.txt", "./cookies.txt", "/etc/cookies.txt", "/tmp/cookies.txt"]
COOKIES_PATH = None
for path in COOKIES_PATHS:
    if os.path.exists(path):
        COOKIES_PATH = path
        print(f"✅ Found cookies: {path}")
        break

app = FastAPI(title="⚡ Sudeep API (Logger + Thumb Fix)")

# ─────────────────────────────
# DATABASE
# ─────────────────────────────
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["MusicAPI_DB12"]
videos_col = db["videos_cacht"]
keys_col = db["api_users"]
queries_col = db["query_mapping"]

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

# 📸 FORCE THUMBNAIL (Jugaad Function)
def get_fallback_thumb(vid_id):
    return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

# 📢 SEND LOG TO TELEGRAM
def send_telegram_log(title, duration, link, vid_id):
    if not BOT_TOKEN: return
    try:
        msg = (
            f"🍫 **ɴᴇᴡ sᴏɴɢ**\n\n"
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

# 🔥 STEP 1: SEARCH ONLY (Metadata + Thumbnail)
def get_video_id_only(query: str):
    ydl_opts = {'quiet': True, 'skip_download': True, 'extract_flat': True, 'noplaylist': True}
    if COOKIES_PATH: ydl_opts['cookiefile'] = COOKIES_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Case A: Direct ID/URL
            direct_id = extract_video_id(query)
            if direct_id:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={direct_id}", download=False)
                # 👇 Thumbnail Check
                thumb = info.get('thumbnail') or get_fallback_thumb(direct_id)
                return direct_id, info.get('title'), format_time(info.get('duration')), thumb

            # Case B: Search Query
            else:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if info and 'entries' in info and info['entries']:
                    v = info['entries'][0]
                    vid_id = v['id']
                    # 👇 Thumbnail Check
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

# 🔥 STEP 2: DOWNLOAD
def auto_download_video(video_id: str):
    random_name = str(uuid.uuid4())
    out = f"/tmp/{random_name}.mp4"
    if os.path.exists(out): os.remove(out)

    cmd = [
        "python", "-m", "yt_dlp", "--js-runtimes", "node", "--no-playlist", "--geo-bypass",
        "-f", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--postprocessor-args", "VideoConvertor:-c:v libx264 -c:a aac -movflags +faststart",
        "-o", out, f"https://www.youtube.com/watch?v={video_id}"
    ]
    if COOKIES_PATH: 
        cmd.insert(3, "--cookies"); cmd.insert(4, COOKIES_PATH)

    try:
        subprocess.run(cmd, check=True, timeout=900)
        return out if os.path.exists(out) and os.path.getsize(out) > 1024 else None
    except: return None

# ─────────────────────────────
# 🔥 AUTH CHECK + USAGE INCREMENT
# ─────────────────────────────
async def verify_and_count(key: str):
    doc = await keys_col.find_one({"api_key": key})

    if not doc or not doc.get("active", True):
        return False, "Invalid/Inactive Key"

    today = str(datetime.date.today())
    if doc.get("last_reset") != today:
        await keys_col.update_one(
            {"api_key": key},
            {"$set": {"used_today": 0, "last_reset": today}}
        )
        doc["used_today"] = 0 

    if doc.get("used_today", 0) >= doc.get("daily_limit", 100):
        return False, "Daily Limit Exceeded"

    await keys_col.update_one(
        {"api_key": key},
        {
            "$inc": {"used_today": 1, "total_usage": 1},
            "$set": {"last_used": time.time()}
        }
    )
    return True, None

# ─────────────────────────────
# 🔥 STATS & HOME
# ─────────────────────────────
@app.get("/stats")
async def get_stats():
    total_songs = await videos_col.count_documents({})
    total_users = await keys_col.count_documents({})
    return {"status": 200, "total_songs": total_songs, "total_users": total_users}

@app.get("/user_stats")
async def user_stats(target_key: str):
    doc = await keys_col.find_one({"api_key": target_key})
    if not doc: return {"status": 404, "error": "Key Not Found"}
    return {
        "user_id": doc.get("user_id"),
        "used_today": doc.get("used_today", 0),
        "total_usage": doc.get("total_usage", 0),
        "daily_limit": doc.get("daily_limit", 100)
    }

@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "Running", "version": "Logger + Thumb Fix"}

# ─────────────────────────────
# MAIN API LOGIC
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    start_time = time.time()

    # 1. Auth Check
    is_valid, err = await verify_and_count(key)
    if not is_valid: return {"status": 403, "error": err}

    clean_query = query.strip().lower()

    # PART A: IDENTIFY VIDEO
    video_id = None
    cached_q = await queries_col.find_one({"query": clean_query})

    title = "Unknown"
    duration = "0:00"
    thumbnail = None

    if cached_q:
        video_id = cached_q["video_id"]
        meta = await videos_col.find_one({"video_id": video_id})
        if meta:
            title = meta.get("title", "Unknown")
            duration = meta.get("duration", "0:00")
            thumbnail = meta.get("thumbnail")

    # Search if not in memory
    if not video_id:
        print(f"🔍 Searching: {query}")
        video_id, title, duration, thumbnail = await asyncio.to_thread(get_video_id_only, query)

        if video_id:
             await queries_col.update_one({"query": clean_query}, {"$set": {"video_id": video_id}}, upsert=True)

    if not video_id: return {"status": 404, "error": "Not Found"}

    # 🔥 FINAL THUMBNAIL CHECK (Agar search se nahi mila toh manually banao)
    if not thumbnail:
        thumbnail = get_fallback_thumb(video_id)

    # PART B: CHECK DATABASE
    cached = await videos_col.find_one({"video_id": video_id})

    if cached and cached.get("catbox_link"):
        print(f"✅ Found in DB: {title}")
        return {
            "status": 200,
            "title": cached.get("title", title),
            "duration": cached.get("duration", duration),
            "link": cached["catbox_link"],
            "id": video_id,
            "thumbnail": cached.get("thumbnail", thumbnail), # ✅ Fixed
            "cached": True,
            "response_time": f"{time.time()-start_time:.2f}s"
        }

    # PART C: DOWNLOAD & SAVE (NEW SONG)
    print(f"⏳ Downloading: {title}")

    # Save Metadata + Thumbnail immediately
    await videos_col.update_one(
        {"video_id": video_id}, 
        {"$set": {
            "video_id": video_id, 
            "title": title, 
            "duration": duration,
            "thumbnail": thumbnail # ✅ Saving to DB
        }}, 
        upsert=True
    )

    file_path = await asyncio.to_thread(auto_download_video, video_id)
    if not file_path: return {"status": 500, "error": "Download Failed"}

    link = await asyncio.to_thread(upload_catbox, file_path)
    if os.path.exists(file_path): os.remove(file_path)

    if not link: return {"status": 500, "error": "Upload Failed"}

    # Update DB
    await videos_col.update_one(
        {"video_id": video_id},
        {"$set": {"catbox_link": link, "cached_at": datetime.datetime.now()}}
    )

    # 📢 SEND TELEGRAM LOG (Background Task)
    asyncio.create_task(asyncio.to_thread(send_telegram_log, title, duration, link, video_id))

    return {
        "status": 200,
        "title": title,
        "duration": duration,
        "link": link,
        "id": video_id,
        "thumbnail": thumbnail, # ✅ Returned
        "cached": False,
        "response_time": f"{time.time()-start_time:.2f}s"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
