from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Union
import os
import requests
from bs4 import BeautifulSoup
import json
import re
from urllib.parse import urlparse, parse_qs
from yt_dlp import YoutubeDL
import logging

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI Setup
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

class VideoURL(BaseModel):
    url: HttpUrl

def human_readable_size(size_bytes: Optional[Union[int, str]]) -> str:
    if size_bytes is None:
        return "Unknown"

    # Ensure size_bytes is an integer, try converting it if it's a string
    try:
        size_bytes = int(size_bytes)
    except (ValueError, TypeError):
        logger.error(f"Invalid size_bytes value: {size_bytes}")
        return "Unknown"

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def extract_video_info(url: str) -> dict:
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        script = soup.find("script", string=re.compile("ytInitialPlayerResponse"))
        if not script:
            raise ValueError("Video information not found")
        data = json.loads(re.search(r"ytInitialPlayerResponse\s*=\s*({.*?});", script.string).group(1))
        return data
    except Exception as e:
        logger.error(f"Failed to extract video info: {e}")
        raise HTTPException(status_code=400, detail="Failed to extract video information")

def parse_streams(data: dict) -> List[dict]:
    formats = data.get("streamingData", {}).get("formats", []) + data.get("streamingData", {}).get("adaptiveFormats", [])
    return [
        {
            "itag": fmt.get("itag"),
            "mimeType": fmt.get("mimeType"),
            "quality": fmt.get("quality"),
            "qualityLabel": fmt.get("qualityLabel"),
            "audioQuality": fmt.get("audioQuality"),
            "fileSize": human_readable_size(int(fmt.get("contentLength", 0)))
        }
        for fmt in formats
    ]

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")

@app.post("/get_streams")
async def get_streams(video: VideoURL):
    data = extract_video_info(str(video.url))
    video_details = data.get("videoDetails", {})
    return {
        "video_title": video_details.get("title"),
        "streams": parse_streams(data),
        "thumbnail_url": video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url")
    }

@app.post("/download_stream")
async def download_stream(video: VideoURL, itag: int, background_tasks: BackgroundTasks):
    data = extract_video_info(str(video.url))
    video_title = data.get("videoDetails", {}).get("title", "video")
    filename = f"{video_title}_{itag}.mp4"
    filename = re.sub(r'[\\/*?:"<>|]', "", filename)  # Remove invalid filename characters

    ydl_opts = {"format": f"{itag}", "outtmpl": filename}
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([str(video.url)])
        
        background_tasks.add_task(os.remove, filename)
        return FileResponse(filename, media_type="application/octet-stream", filename=filename)
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise HTTPException(status_code=500, detail="Download failed")