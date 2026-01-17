import os
import time
import datetime
import requests
import re
import asyncio
import uuid
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import config

app = FastAPI(title="⚡ Sudeep API (Hybrid + Title Fix)")

# ─────────────────────────────
# DATABASE & CONFIG
# ─────────────────────────────
if not config.MONGO_DB_URI:
    print("⚠️ MONGO_DB_URI not found.")

mongo = AsyncIOMotorClient(config.MONGO_DB_URI)
db = mongo["MusicAPI_DB12"]
videos_col = db["videos_cacht"]
keys_col = db["api_users"]
queries_col = db["query_mapping"]

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"

# ─────────────────────────────
# 🧹 TITLE CLEANER (NEW FEATURE)
# ─────────────────────────────
def clean_song_title(title):
    """
    YouTube ke lambe titles ko saaf karke sirf Gaane ka naam nikalta hai.
    Example: "Song (Full Video) | Movie" -> "Song"
    """
    if not title: return ""
    
    # 1. Bracket ke andar ka maal uda do: (Full Video), [Official], etc.
    title = re.sub(r"[\(\[\{].*?[\)\]\}]", "", title)
    
    # 2. Pipes (|) se pehle ka hissa lo (zyadatar asli naam yahin hota hai)
    if "|" in title:
        title = title.split("|")[0]
    
    # 3. Famous faltu words hata do
    keywords = ["Full Video", "Lyrical", "Official Video", "Audio", "Vs", "Feat", "ft."]
    for word in keywords:
        title = re.sub(f"(?i){word}", "", title)
        
    return title.strip()

# ─────────────────────────────
# 🔑 KEY ROTATION LOGIC
# ─────────────────────────────
current_key_index = 0

def get_next_key():
    global current_key_index
    keys = config.YOUTUBE_API_KEYS
    if not keys: return None
    key = keys[current_key_index]
    current_key_index = (current_key_index + 1) % len(keys)
    return key

def format_duration(seconds):
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    except: return "0:00"

def get_fallback_thumb(vid_id):
    return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

def send_telegram_log(title, duration, link, vid_id):
    if not config.BOT_TOKEN: return
    try:
        msg = (
            f"🍫 **ɴᴇᴡ sᴏɴɢ (Hybrid)**\n\n"
            f"🫶 **ᴛɪᴛʟᴇ:** {title}\n"
            f"⏱ **ᴅᴜʀᴀᴛɪᴏɴ:** {duration}\n"
            f"🛡️ **ɪᴅ:** `{vid_id}`\n"
            f"👀 [ʟɪɴᴋ]({link})\n\n"
            f"🍭 @Kaito_3_2"
        )
        requests.post(
            f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
            data={"chat_id": config.LOGGER_ID, "text": msg, "parse_mode": "Markdown"}
        )
    except Exception as e:
        print(f"❌ Logger Error: {e}")

# ─────────────────────────────
# 🔥 STEP 1: GOOGLE OFFICIAL API (Metadata Only)
# ─────────────────────────────
def get_youtube_metadata(query):
    for _ in range(3):
        api_key = get_next_key()
        if not api_key: break
        
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": api_key}

        try:
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()

            if "error" in data:
                print(f"⚠️ Key Error: {data['error']['message']}")
                continue 

            if "items" in data and len(data["items"]) > 0:
                item = data["items"][0]
                return {
                    "id": item["id"]["videoId"],
                    "title": item["snippet"]["title"],
                    "thumbnail": item["snippet"]["thumbnails"]["high"]["url"]
                }
        except: continue
    return None

# ─────────────────────────────
# 🔥 STEP 2: JIOSAAVN AUDIO FINDER
# ─────────────────────────────
def get_jiosaavn_direct_link(song_title):
    print(f"🎵 Searching JioSaavn for: {song_title}")
    try:
        search_url = f"{config.JIOSAAVN_URL}/search?query={song_title}"
        resp = requests.get(search_url, timeout=5).json()

        if not resp.get("results"): return None

        song_id = resp["results"][0]["id"]
        details_url = f"{config.JIOSAAVN_URL}/song?id={song_id}"
        details = requests.get(details_url, timeout=5).json()
        
        target = details[0] if isinstance(details, list) else details

        media_urls = target.get("media_urls", {})
        link = media_urls.get("320_KBPS") or media_urls.get("160_KBPS") or target.get("media_url")
        
        dur_sec = target.get("duration", 0)
        return {"link": link, "duration": format_duration(dur_sec)}

    except Exception as e:
        print(f"❌ JioSaavn Error: {e}")
        return None

# ─────────────────────────────
# 🔥 STEP 3: BRIDGE (With SSL Fix)
# ─────────────────────────────
def bridge_jio_to_catbox(jio_url):
    random_name = str(uuid.uuid4())
    temp_file = f"/tmp/{random_name}.m4a"
    
    try:
        print("📥 Downloading Audio from JioSaavn...")
        # verify=False isliye taaki SSL error na aaye
        with requests.get(jio_url, stream=True, timeout=30, verify=False) as r:
            if r.status_code == 404:
                print("❌ Link Expired/Broken (404)")
                return None
            r.raise_for_status()
            with open(temp_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        print("📤 Uploading to Catbox...")
        if os.path.exists(temp_file):
            with open(temp_file, "rb") as f:
                r = requests.post(CATBOX_UPLOAD, data={"reqtype": "fileupload"}, files={"fileToUpload": f}, timeout=120)
            os.remove(temp_file)
            
            if r.status_code == 200 and r.text.startswith("http"):
                return r.text.strip()
            else:
                print(f"❌ Catbox Error: {r.text}")

    except Exception as e:
        print(f"❌ Bridge Error: {e}")
        if os.path.exists(temp_file): os.remove(temp_file)
    
    return None

# ─────────────────────────────
# 🔐 AUTH CHECK
# ─────────────────────────────
async def verify_and_count(key: str):
    doc = await keys_col.find_one({"api_key": key})
    if not doc or not doc.get("active", True): return False, "Invalid Key"
    await keys_col.update_one({"api_key": key}, {"$inc": {"total_usage": 1}})
    return True, None

# ─────────────────────────────
# 🚀 MAIN API LOGIC
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    start_time = time.time()

    # 1. Auth Check
    is_valid, err = await verify_and_count(key)
    if not is_valid: return {"status": 403, "error": err}

    clean_query = query.strip().lower()

    # 2. Get Metadata (Google API)
    print(f"🔍 Searching YouTube: {query}")
    yt_data = await asyncio.to_thread(get_youtube_metadata, query)
    
    if not yt_data: return {"status": 404, "error": "Song Not Found on YouTube"}

    video_id = yt_data["id"]
    original_title = yt_data["title"]
    thumbnail = yt_data["thumbnail"]

    # 3. DB Check
    cached = await videos_col.find_one({"video_id": video_id})
    if cached and cached.get("catbox_link"):
        print(f"✅ Found in Cache: {original_title}")
        return {
            "status": 200, "title": cached.get("title", original_title),
            "link": cached["catbox_link"], "id": video_id,
            "thumbnail": cached.get("thumbnail", thumbnail), "cached": True,
            "duration": cached.get("duration", "0:00"),
            "response_time": f"{time.time()-start_time:.2f}s"
        }

    # 4. Fetch New Song (Logic Updated for Cleaner Title) 🛠️
    print(f"⏳ New Song Detected: {original_title}")

    # A. Clean Title Try karo
    clean_title = clean_song_title(original_title)
    print(f"🧹 Cleaned Title for Search: '{clean_title}'")
    
    jio_data = await asyncio.to_thread(get_jiosaavn_direct_link, clean_title)
    
    # B. Fallback: Agar clean title se na mile, toh original se try karo
    if not jio_data or not jio_data["link"]:
        print("⚠️ Clean search failed, trying original title...")
        jio_data = await asyncio.to_thread(get_jiosaavn_direct_link, original_title)

    if not jio_data or not jio_data["link"]:
        return {"status": 500, "error": "Audio Not Found on JioSaavn"}
    
    # C. Bridge (Download & Upload)
    catbox_link = await asyncio.to_thread(bridge_jio_to_catbox, jio_data["link"])
    
    if not catbox_link:
        return {"status": 500, "error": "Upload Failed (Bridge Error)"}

    # D. Save to DB
    duration = jio_data["duration"]
    await videos_col.update_one(
        {"video_id": video_id},
        {"$set": {
            "title": original_title, # DB me Original sundar title hi rakhenge
            "video_id": video_id,
            "catbox_link": catbox_link,
            "thumbnail": thumbnail,
            "duration": duration,
            "cached_at": datetime.datetime.now()
        }}, upsert=True
    )
    
    await queries_col.update_one({"query": clean_query}, {"$set": {"video_id": video_id}}, upsert=True)
    asyncio.create_task(asyncio.to_thread(send_telegram_log, original_title, duration, catbox_link, video_id))

    return {
        "status": 200, "title": original_title, "duration": duration,
        "link": catbox_link, "id": video_id, "thumbnail": thumbnail,
        "cached": False, "response_time": f"{time.time()-start_time:.2f}s"
    }

# Stats & Home Routes
@app.get("/stats")
async def get_stats():
    total_songs = await videos_col.count_documents({})
    return {"status": 200, "total_songs": total_songs}

@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "Running", "mode": "Hybrid (Google+Jio+Catbox)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
