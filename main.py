import os
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from youtubesearchpython import VideosSearch

# ─────────────────────────────
# APP SETUP
# ─────────────────────────────
app = FastAPI(title="Sudeep Music API ⚡ Ultra Fast")

MONGO_URL = os.getenv("MONGO_DB_URI")
client = AsyncIOMotorClient(MONGO_URL)
db = client["MusicAPI_DB"]
collection = db["songs_cache"]

# 🔥 In-memory ultra fast cache
MEM_CACHE = {}

# ─────────────────────────────
# HELPERS
# ─────────────────────────────
def extract_video_id(query: str):
    query = query.strip()

    if len(query) == 11 and " " not in query:
        return query

    if "v=" in query:
        return query.split("v=")[1].split("&")[0]

    if "youtu.be/" in query:
        return query.split("youtu.be/")[1].split("?")[0]

    return None


# ─────────────────────────────
# MAIN API
# ─────────────────────────────
@app.get("/get")
async def get_music(query: str):
    video_id = extract_video_id(query)

    # 1️⃣ RAM CACHE (FASTEST)
    if video_id and video_id in MEM_CACHE:
        return MEM_CACHE[video_id]

    # 2️⃣ DATABASE CACHE
    if video_id:
        cached = await collection.find_one(
            {"video_id": video_id},
            {"_id": 0}
        )
        if cached:
            response = {
                "t": cached["title"],
                "u": cached["catbox_link"]
            }
            MEM_CACHE[video_id] = response
            return response

    # 3️⃣ YOUTUBE SEARCH (LAST OPTION)
    try:
        search = VideosSearch(query, limit=1, timeout=3)
        res = search.result().get("result")

        if not res:
            raise Exception("No results")

        yt_id = res[0]["id"]
        yt_title = res[0]["title"]

        # Search result DB cache
        cached = await collection.find_one(
            {"video_id": yt_id},
            {"_id": 0}
        )
        if cached:
            response = {
                "t": cached.get("title", yt_title),
                "u": cached["catbox_link"]
            }
            MEM_CACHE[yt_id] = response
            return response

        # ❌ Not cached yet
        return {
            "error": "NOT_CACHED",
            "video_id": yt_id,
            "title": yt_title,
            "message": "Song mila par DB me nahi hai. Pehle bot se download karao."
        }

    except Exception:
        raise HTTPException(
            status_code=404,
            detail="YouTube search failed or blocked"
        )


@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "ok", "cache_items": len(MEM_CACHE)}
