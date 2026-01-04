import os, time, datetime, subprocess, requests
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from youtubesearchpython import VideosSearch

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CONTACT = "@Kaito_3_2"

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"
COOKIES_PATH = "/app/cookies.txt"

# ─────────────────────────────
# APP
# ─────────────────────────────
app = FastAPI(title="Sudeep Music API ⚡ Video Auto")

client = AsyncIOMotorClient(MONGO_URL)
db = client["MusicAPI_DB1"]

videos_col = db["videos_cachet"]
keys_col = db["api_users"]

MEM_CACHE = {}

# ─────────────────────────────
# API KEY VERIFY
# ─────────────────────────────
async def verify_api_key(key: str):
    doc = await keys_col.find_one({"api_key": key, "active": True})
    if not doc:
        return False, "Invalid API key", None

    now = int(time.time())
    if now > doc["expires_at"]:
        await send_dm(
            doc["user_id"],
            f"⚠️ Your API key has expired.\n\nContact {ADMIN_CONTACT} to renew."
        )
        return False, "API key expired", doc

    today = str(datetime.date.today())
    if doc.get("last_reset") != today:
        await keys_col.update_one(
            {"api_key": key},
            {"$set": {"used_today": 0, "last_reset": today}}
        )
        doc["used_today"] = 0

    if doc["used_today"] >= doc["daily_limit"]:
        return False, "Daily limit exceeded", doc

    await keys_col.update_one(
        {"api_key": key},
        {"$inc": {"used_today": 1}}
    )

    return True, None, doc

# ─────────────────────────────
# HELPERS
# ─────────────────────────────
def yt_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

def extract_video_id(q):
    q = q.strip()
    if len(q) == 11 and " " not in q:
        return q
    if "v=" in q:
        return q.split("v=")[1].split("&")[0]
    if "youtu.be/" in q:
        return q.split("youtu.be/")[1].split("?")[0]
    return None

def search_youtube(query):
    try:
        s = VideosSearch(query, limit=1)
        r = s.result().get("result")
        if not r:
            return None, None
        return r[0]["id"], r[0]["title"]
    except:
        return None, None

async def send_dm(user_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": user_id, "text": text},
            timeout=10
        )
    except:
        pass

def upload_catbox(path):
    with open(path, "rb") as f:
        r = requests.post(
            CATBOX_UPLOAD,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            timeout=120
        )
    if r.text.startswith("https://"):
        return r.text.strip()
    raise Exception("Catbox upload failed")

def auto_download_video(video_id):
    out = f"/tmp/{video_id}.mp4"
    cmd = [
        "python", "-m", "yt_dlp",
        "--cookies", COOKIES_PATH,
        "--js-runtimes", "node",
        "--no-playlist",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        yt_url(video_id),
        "-o", out
    ]
    subprocess.run(cmd, check=True, timeout=900)
    return out

# ─────────────────────────────
# MAIN VIDEO API
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str = None):

    if not key:
        return {"status": 401, "error": "API key required"}

    ok, err, _ = await verify_api_key(key)
    if not ok:
        return {"status": 403, "error": err}

    video_id = extract_video_id(query)
    title = None

    if not video_id:
        video_id, title = search_youtube(query)
        if not video_id:
            return {"status": 404, "title": None, "link": None, "video_id": None}

    if video_id in MEM_CACHE:
        return MEM_CACHE[video_id]

    cached = await videos_col.find_one({"video_id": video_id})
    if cached:
        resp = {
            "status": 200,
            "title": cached["title"],
            "link": cached["catbox_link"],
            "video_id": video_id
        }
        MEM_CACHE[video_id] = resp
        return resp

    file_path = auto_download_video(video_id)
    catbox = upload_catbox(file_path)

    await videos_col.update_one(
        {"video_id": video_id},
        {"$set": {
            "video_id": video_id,
            "title": title or video_id,
            "catbox_link": catbox
        }},
        upsert=True
    )

    resp = {
        "status": 200,
        "title": title or video_id,
        "link": catbox,
        "video_id": video_id
    }
    MEM_CACHE[video_id] = resp
    return resp

# ─────────────────────────────
# HEALTH
# ─────────────────────────────
@app.get("/")
async def home():
    return {"status": 200}
