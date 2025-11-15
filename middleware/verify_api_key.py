from fastapi import Request, HTTPException
import os

def verify_api_key(request: Request):
    SHARED_SECRET = os.getenv("SHARED_SECRET")
    key = request.headers.get("x-api-key")
    
    if key != SHARED_SECRET:
        raise HTTPException(401, "Unauthorized")
