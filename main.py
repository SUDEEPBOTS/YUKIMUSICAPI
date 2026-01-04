import os
from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from youtubesearchpython import VideosSearch # Smart Search ke liye

# --- CONFIGURATION ---
app = FastAPI(
    title="Sudeep's Smart Music Vault",
    description="Ye API sirf Database se gaane uthati hai. No Download, Only Speed. ⚡"
)

# Heroku Config Var se URL uthayega
MONGO_URL = os.getenv("MONGO_DB_URI") 

# --- DATABASE CONNECTION ---
try:
    if not MONGO_URL:
        print("❌ Error: MONGO_DB_URI set nahi hai!")
        collection = None
    else:
        client = MongoClient(MONGO_URL)
        # ⚠️ WAJI SAME DB NAME JO MUSIC BOT KA HAI
        db = client["MusicAPI_DB"]
        collection = db["songs_cache"]
        print("✅ API Connected to Music Bot Database!")
except Exception as e:
    print(f"❌ DB Connection Error: {e}")
    collection = None

# --- HELPER: NAME TO ID CONVERTER ---
def get_video_id(query):
    try:
        # Agar user ne direct link diya hai
        if "youtube.com" in query or "youtu.be" in query:
            if "v=" in query:
                return query.split("v=")[1].split("&")[0]
            elif "youtu.be/" in query:
                return query.split("youtu.be/")[1].split("?")[0]
        
        # Agar naam diya hai (Smart Search)
        search = VideosSearch(query, limit=1)
        result = search.result()['result'][0]
        return result['id'], result['title']
    except:
        return None, None

# --- MAIN ENDPOINT ---
@app.get("/")
def home():
    return {"status": "Online", "mode": "Read-Only (Super Fast)"}

@app.get("/get")
def get_music(query: str):
    if collection is None:
        return {"status": "error", "message": "Database not connected"}

    # 1. Pehle ID nikalo (Smart Search)
    video_id, title = get_video_id(query)

    if not video_id:
        raise HTTPException(status_code=404, detail="Youtube par ye gaana nahi mila.")

    # 2. AB DATABASE (LOCKER) CHECK KARO
    # Hum 'find_one' use karenge jo super fast hai
    cached_song = collection.find_one({"video_id": video_id})

    # 3. LOGIC: MAAL HAI YA NAHI?
    if cached_song and cached_song.get("catbox_link"):
        return {
            "status": "success",
            "found_in_db": True,
            "title": cached_song.get("title", title),
            "video_id": video_id,
            "download_link": cached_song["catbox_link"],
            "source": "Sudeep's Database ⚡"
        }
    else:
        # ⚠️ Yahan hum saaf mana kar denge (Jaisa tune bola)
        return {
            "status": "failed",
            "found_in_db": False,
            "message": "Ye gaana abhi tak Music Bot ne kidnap nahi kiya hai. Pehle Bot par bajao!",
            "video_id": video_id,
            "title": title
        }
      
