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

app = FastAPI(title="⚡ Sudeep API (Search-First Mode)")

# ─────────────────────────────
# DATABASE
# ─────────────────────────────
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["MusicAPI_DB12"]
videos_col = db["videos_cacht"]
keys_col = db["api_users"]
queries_col = db["query_mapping"]

# ─────────────────────────────
# FUNCTIONS
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

# 🔥 STEP 1: SEARCH ONLY (ID Nikaalne ke liye)
def get_video_id_only(query: str):
    # 'extract_flat' True = No Download, Just Metadata
    ydl_opts = {'quiet': True, 'skip_download': True, 'extract_flat': True, 'noplaylist': True}
    if COOKIES_PATH: ydl_opts['cookiefile'] = COOKIES_PATH
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Agar direct Video ID/URL hai
            direct_id = extract_video_id(query)
            if direct_id:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={direct_id}", download=False)
                return direct_id, info.get('title'), format_time(info.get('duration'))
            
            # Agar Search Query hai
            else:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if info and 'entries' in info and info['entries']:
                    v = info['entries'][0]
                    return v['id'], v['title'], format_time(v.get('duration'))
    except Exception as e:
        print(f"Search Error: {e}")
    return None, None, None

def upload_catbox(path: str):
    try:
        with open(path, "rb") as f:
            r = requests.post(CATBOX_UPLOAD, data={"reqtype": "fileupload"}, files={"fileToUpload": f}, timeout=120)
        return r.text.strip() if r.status_code == 200 and r.text.startswith("http") else None
    except: return None

# 🔥 STEP 2: DOWNLOAD (Sirf jab DB mein na ho)
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

# Key Check
async def verify_key_fast(key: str):
    try:
        doc = await keys_col.find_one({"api_key": key, "active": True})
        if not doc: return False
        return True
    except: return False

# ─────────────────────────────
# 🔥 UPTIME ENDPOINT (24/7 Keep Alive)
# ─────────────────────────────
@app.get("/")
def home():
    return {"status": "Running", "mode": "Search-First"}

# ─────────────────────────────
# MAIN API
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    start_time = time.time()
    
    # 1. Auth Check
    if not await verify_key_fast(key): return {"status": 403, "error": "Invalid Key"}

    clean_query = query.strip().lower()
    
    # ──────────────────────────────────────────────────
    # PART A: IDENTIFY VIDEO ID (Search First) 🔍
    # ──────────────────────────────────────────────────
    
    # 1. Pehle Memory Check (Query -> ID)
    video_id = None
    cached_q = await queries_col.find_one({"query": clean_query})
    
    if cached_q:
        video_id = cached_q["video_id"]
        # DB se title/duration utha lo
        meta = await videos_col.find_one({"video_id": video_id})
        title = meta["title"] if meta else "Unknown"
        duration = meta["duration"] if meta else "0:00"
        print(f"🧠 Memory Match: {clean_query} -> {video_id}")
    
    # 2. Agar Memory mein nahi hai, to YouTube Search karo (Wait 2-3s)
    if not video_id:
        print(f"🔍 Searching YouTube for: {query}")
        video_id, title, duration = await asyncio.to_thread(get_video_id_only, query)
        
        # Mapping Save kar lo
        if video_id:
             await queries_col.update_one({"query": clean_query}, {"$set": {"video_id": video_id}}, upsert=True)

    if not video_id: return {"status": 404, "error": "Not Found"}

    # ──────────────────────────────────────────────────
    # PART B: CHECK DATABASE (The Magic Step) ✨
    # ──────────────────────────────────────────────────
    
    # ID mil gayi. Ab check karo kya ye Downloaded hai?
    cached = await videos_col.find_one({"video_id": video_id})
    
    # Agar Link hai -> Instant Return (SKIP DOWNLOAD)
    if cached and cached.get("catbox_link"):
        print(f"✅ Found in DB: {title}")
        return {
            "status": 200,
            "title": cached["title"],
            "duration": cached["duration"],
            "link": cached["catbox_link"],
            "id": video_id,  # <--- YE LINE JOD DE
            "cached": True,
            "response_time": f"{time.time()-start_time:.2f}s"
        }

    # ──────────────────────────────────────────────────
    # PART C: DOWNLOAD (Only if DB fails) ⬇️
    # ──────────────────────────────────────────────────
    
    print(f"⏳ Not in DB. Downloading: {title}")
    
    # 1. Metadata save (Status: Processing)
    await videos_col.update_one(
        {"video_id": video_id}, 
        {"$set": {"video_id": video_id, "title": title, "duration": duration}}, 
        upsert=True
    )

    # 2. Download File
    file_path = await asyncio.to_thread(auto_download_video, video_id)
    if not file_path: return {"status": 500, "error": "Download Failed"}

    # 3. Upload File
    link = await asyncio.to_thread(upload_catbox, file_path)
    if os.path.exists(file_path): os.remove(file_path)

    if not link: return {"status": 500, "error": "Upload Failed"}

    # 4. Save Link to DB
    await videos_col.update_one(
        {"video_id": video_id},
        {"$set": {"catbox_link": link, "cached_at": datetime.datetime.now()}}
    )

    return {
        "status": 200,
        "title": title,
        "duration": duration,
        "link": link,
        "id": video_id,  #
        "cached": False,
        "response_time": f"{time.time()-start_time:.2f}s"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    
