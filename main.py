from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import yt_dlp
import asyncio
import os
import time
import random
from datetime import datetime

app = FastAPI(title="Harmony YouTube Downloader API", version="1.0.0")

# CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchRequest(BaseModel):
    query: str
    max_results: Optional[int] = 10

class VideoInfo(BaseModel):
    id: str
    title: str
    channel: str
    duration: str
    thumbnail: str
    view_count: Optional[int] = None
    upload_date: Optional[str] = None

class DownloadRequest(BaseModel):
    video_id: str
    format: Optional[str] = "bestaudio/best"

# Custom headers to mimic browser behavior
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

async def retry_yt_dlp_operation(operation, max_retries=3, initial_delay=2):
    """Retry yt-dlp operation with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return await operation()
        except yt_dlp.utils.DownloadError as e:
            if "Sign in to confirm you're not a bot" in str(e) and attempt < max_retries - 1:
                # Exponential backoff with jitter
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"Bot detection triggered, retrying in {delay:.2f} seconds (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"Error occurred, retrying in {delay:.2f} seconds (attempt {attempt + 1}/{max_retries}): {str(e)}")
                await asyncio.sleep(delay)
                continue
            raise
    raise Exception("Max retries exceeded")

@app.post("/api/search", response_model=List[VideoInfo])
async def search_videos(request: SearchRequest):
    """Search YouTube videos with retry logic"""
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'default_search': f'ytsearch{request.max_results}',
            'http_headers': DEFAULT_HEADERS,
            'socket_timeout': 30,
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_client': ['android', 'web'],
                }
            },
        }
        
        results = []

        def extract_info(info_dict):
            return VideoInfo(
                id=info_dict.get('id', ''),
                title=info_dict.get('title', 'No title'),
                channel=info_dict.get('uploader', 'Unknown channel'),
                duration=format_duration(info_dict.get('duration')),
                thumbnail=info_dict.get('thumbnail', ''),
                view_count=info_dict.get('view_count', 0),
                upload_date=info_dict.get('upload_date', '')
            )
        
        async def search_operation():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_query = f"ytsearch{request.max_results}:{request.query}"
                info = await asyncio.to_thread(ydl.extract_info, search_query, download=False)
                
                if 'entries' in info:
                    for entry in info['entries']:
                        if entry:
                            results.append(extract_info(entry))
                return results
        
        # Use retry logic for the search operation
        return await retry_yt_dlp_operation(search_operation)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.post("/api/download/{video_id}")
async def download_audio(video_id: str, format: str = "bestaudio/best"):
    """Download audio from YouTube video with retry logic"""
    try:
        # Create downloads directory if it doesn't exist
        os.makedirs("downloads", exist_ok=True)
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"downloads/{video_id}_{timestamp}.%(ext)s"
        
        ydl_opts = {
            'format': format,
            'outtmpl': filename,
            'quiet': False,
            'http_headers': DEFAULT_HEADERS,
            'socket_timeout': 30,
            'progress_hooks': [progress_hook],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_client': ['android', 'web'],
                }
            },
        }
        
        async def download_operation():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                url = f"https://www.youtube.com/watch?v={video_id}"
                info = await asyncio.to_thread(ydl.extract_info, url, download=True)
                
                # Get the actual filename (convert extensions to mp3)
                actual_filename = ydl.prepare_filename(info).replace('.webm', '.mp3').replace('.m4a', '.mp3')
                
                return {
                    "status": "success",
                    "filename": actual_filename,
                    "title": info.get('title', ''),
                    "duration": format_duration(info.get('duration'))
                }
        
        # Use retry logic for the download operation
        return await retry_yt_dlp_operation(download_operation)
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")


def progress_hook(d):
    """Progress hook for download updates"""
    if d['status'] == 'downloading':
        print(f"Downloading: {d.get('_percent_str', '0%')}")
    elif d['status'] == 'finished':
        print("Download completed, converting...")


def format_duration(seconds: Optional[int]) -> str:
    """Convert seconds to MM:SS format, safe for None"""
    if not seconds or not isinstance(seconds, int):
        return "0:00"
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}:{sec:02d}"


@app.get("/")
async def root():
    return {"message": "Harmony YouTube Downloader API", "status": "running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
