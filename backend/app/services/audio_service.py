import os
import shutil
from fastapi import UploadFile
from app.config import settings

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}

def is_allowed_file(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTENSIONS

def save_uploaded_audio(upload_file: UploadFile) -> str:
    """Saves the uploaded file to the upload directory and returns the absolute path."""
    # Create clean filename
    filename = upload_file.filename or "audio"
    clean_filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
    
    # Prefix with timestamp or uuid to prevent collision
    import time
    timestamp = int(time.time())
    unique_filename = f"{timestamp}_{clean_filename}"
    
    file_path = os.path.join(settings.AUDIO_UPLOAD_DIR, unique_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
        
    return file_path
