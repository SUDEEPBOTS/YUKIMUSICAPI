import os
import uuid
import subprocess
import requests
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")          # logger ke liye
LOG_GROUP_ID = os.getenv("LOG_GROUP_ID")    # logger GC id (e.g. -100xxxx)

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"
COOKIES_PATH = "/app/cookies.txt"           # ROOT cookies.txt

# ─────────────────────────────
# APP
# ─────────────────────────────
app = FastAPI(title="Sudeep Music API ⚡ Auto Fetch")

client = AsyncIOMotorClient(MONGO_URL)
db = client["MusicAPI_DB1"]
collection = db["songs_cache"]

MEM_CACHE = {}

# ─────────────────────────────
# HELPERS
# ─────────────────────────────
def yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"

def send_logger(text: str):
    if not BOT_TOKEN or not LOG_GROUP_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": LOG_GROUP_ID, "text": text}
        )
    except:
        pass

def upload_catbox(file_path: str) -> str:
    with open(file_path, "rb") as f:
        r = requests.post(
            CATBOX_UPLOAD,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            timeout=60
        )
    if r.status_code == 200 and r.text.startswith("https://"):
        return r.text.strip()
    raise Exception("Catbox upload failed")

def extract_video_id(query: str):
    q = query.strip()
    if len(q) == 11 and " " not in q:
        return q
    if "v=" in q:
        return q.split("v=")[1].split("&")[0]
    if "youtu.be/" in q:
        return q.split("youtu.be/")[1].split("?")[0]
    return None

def auto_download(video_id: str) -> str:
    """
    yt-dlp + ffmpeg via yt-dlp
    output: /tmp/<id>.mp3
    """
    out = f"/tmp/{video_id}.mp3"
    cmd = [
        "yt-dlp",
        "--cookies", COOKIES_PATH,
        "-f", "bestaudio",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        yt_url(video_id),
        "-o", out
    ]
    subprocess.run(cmd, check=True)
    return out

# ─────────────────────────────
# MAIN API
# ─────────────────────────────
@app.get("/get")
async def get_music(query: str):
    video_id = extract_video_id(query)
    if not video_id:
        raise HTTPException(400, "Invalid query / video id")

    # 1️⃣ RAM cache
    if video_id in MEM_CACHE:
        return MEM_CACHE[video_id]

    # 2️⃣ DB cache
    cached = await collection.find_one({"video_id": video_id}, {"_id": 0})
    if cached:
        resp = {"t": cached["title"], "u": cached["catbox_link"], "id": video_id}
        MEM_CACHE[video_id] = resp
        return resp

    # 3️⃣ AUTO DOWNLOAD
    try:
        local_file = auto_download(video_id)
        catbox_link = upload_catbox(local_file)

        title = video_id  # simple; bot side se YouTube title aa jaayega
        doc = {
            "video_id": video_id,
            "title": title,
            "catbox_link": catbox_link
        }
        await collection.insert_one(doc)

        resp = {"t": title, "u": catbox_link, "id": video_id}
        MEM_CACHE[video_id] = resp

        send_logger(
            f"🎵 New Song Added Automatically\n"
            f"🆔 {video_id}\n"
            f"📦 Catbox\n"
            f"🔗 {catbox_link}"
        )

        return resp

    except Exception as e:
        raise HTTPException(500, f"Auto download failed: {e}")

# ─────────────────────────────
# HEALTH (UPTIME)
# ─────────────────────────────
@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "ok", "cache_items": len(MEM_CACHE)}
