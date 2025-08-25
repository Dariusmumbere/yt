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
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = FastAPI(title="Harmony YouTube Downloader API", version="1.0.0")

# CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# YouTube Data API configuration
YOUTUBE_API_KEY = "AIzaSyCCJa0xel2ISGG3MG8VCmV6pMEZF9joDFM"
youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

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
    """Search YouTube videos using YouTube Data API"""
    try:
        # Use YouTube Data API for search
        search_response = youtube.search().list(
            q=request.query,
            part="snippet",
            maxResults=request.max_results,
            type="video"
        ).execute()
        
        # Extract video IDs for getting additional details
        video_ids = [item['id']['videoId'] for item in search_response['items']]
        
        # Get video details (including duration)
        videos_response = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(video_ids)
        ).execute()
        
        results = []
        for item in videos_response['items']:
            # Format duration from ISO 8601 to readable format
            duration = parse_duration(item['contentDetails']['duration'])
            
            # Format upload date
            upload_date = format_date(item['snippet']['publishedAt'])
            
            # Get view count
            view_count = int(item['statistics'].get('viewCount', 0))
            
            # Get thumbnail (highest resolution available)
            thumbnails = item['snippet']['thumbnails']
            thumbnail = thumbnails.get('high', thumbnails.get('medium', thumbnails.get('default', {}))).get('url', '')
            
            results.append(VideoInfo(
                id=item['id'],
                title=item['snippet']['title'],
                channel=item['snippet']['channelTitle'],
                duration=duration,
                thumbnail=thumbnail,
                view_count=view_count,
                upload_date=upload_date
            ))
        
        return results
        
    except HttpError as e:
        raise HTTPException(status_code=500, detail=f"YouTube API error: {str(e)}")
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


def parse_duration(duration_str: str) -> str:
    """Parse ISO 8601 duration format to MM:SS"""
    import isodate
    try:
        duration = isodate.parse_duration(duration_str)
        total_seconds = int(duration.total_seconds())
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    except:
        return "0:00"


def format_date(date_str: str) -> str:
    """Format ISO date to YYYY-MM-DD"""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d")
    except:
        return date_str[:10] if len(date_str) >= 10 else ""


@app.get("/")
async def root():
    return {"message": "Harmony YouTube Downloader API", "status": "running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
