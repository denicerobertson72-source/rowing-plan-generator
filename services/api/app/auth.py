"""Supabase Auth verification for API ownership checks."""
from __future__ import annotations
import os
import httpx
from typing import Optional
from fastapi import Header, HTTPException

def current_user_id(authorization: Optional[str] = Header(default=None)) -> str:
    if os.getenv("REQUIRE_AUTH", "false").lower() != "true": return "development-user"
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Sign in is required.")
    url,key=os.getenv("SUPABASE_URL"),os.getenv("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key: raise HTTPException(503,"Authentication is not configured.")
    response=httpx.get(f"{url}/auth/v1/user",headers={"apikey":key,"Authorization":authorization},timeout=8)
    if response.status_code != 200: raise HTTPException(401,"Your session is not valid.")
    return response.json()["id"]
