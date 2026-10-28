import os
import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")

def api_get(path, **params):
    params = {k: v for k, v in params.items() if v not in (None, "")}
    r = requests.get(f"{API_URL}{path}", params=params, timeout=60)
    if r.status_code >= 400: 
        try: msg = r.json().get("detail", r.text)
        except Exception: msg = r.text
        raise RuntimeError(msg)
    return r.json()

def api_post(path, body):
    r = requests.post(f"{API_URL}{path}", json=body, timeout=60)
    if r.status_code >= 400: 
        try: msg = r.json().get("detail", r.text)
        except Exception: msg = r.text
        raise RuntimeError(msg)
    return r.json()

@st.cache_data(ttl=300)
def get_genres(): 
    return api_get("/genres")

@st.cache_data(ttl=10)
def get_users(): 
    return api_get("/users")