import os
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from recsys import NotEnoughData, Recommender

app = FastAPI(title="Film Recommender API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
rs = Recommender()

class RatingIn(BaseModel):
    user_id: int
    movie_id: int
    rating: float = Field(..., ge=0.5, le=5)

class UserIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)

def _call(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except KeyError as e: raise HTTPException(404, f"Не найдено: {e.args[0]}")
    except NotEnoughData as e: raise HTTPException(422, str(e))
    except ValueError as e: raise HTTPException(400, str(e))

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/popular")
def popular(n: int = Query(10, ge=1, le=50), genre: Optional[str] = Query(None)):
    return rs.popular(n, genre)

@app.get("/genres")
def genres(): return rs.genres()

@app.get("/movies/search")
def search(q: str = Query(""), n: int = Query(20, ge=1, le=100)): return rs.search(q, n)

@app.get("/movies/{movie_id}")
def movie(movie_id: int): return _call(rs.get_movie, movie_id)

@app.get("/movies/{movie_id}/similar/cf")
def similar_cf(movie_id: int, n: int = Query(10, ge=1, le=50)): return _call(rs.similar_cf, movie_id, n)

@app.get("/users/{user_id}/recommendations/user-cf")
def recommend_user_cf(user_id: int, n: int = Query(10, ge=1, le=50), k: int = Query(30, ge=5, le=200)):
    return _call(rs.recommend_user_cf, user_id, n, k)

@app.get("/movies/{movie_id}/similar/content")
def similar_content(movie_id: int, n: int = Query(10, ge=1, le=50)): return _call(rs.similar_content, movie_id, n)

@app.get("/users/{user_id}/recommendations")
def personal(user_id: int, n: int = Query(10, ge=1, le=50), method: Literal["content", "hybrid"] = Query("content"), alpha: float = Query(0.7)):
    return _call(rs.recommend_personal, user_id, n, method, alpha)

@app.get("/users")
def users(): return rs.list_users()

@app.post("/users", status_code=201)
def create_user(body: UserIn): return _call(rs.create_user, body.name)

@app.get("/users/{user_id}/ratings")
def user_ratings(user_id: int): return _call(rs.rated_by_user, user_id)

@app.post("/ratings")
def rate(body: RatingIn): return _call(rs.add_rating, body.user_id, body.movie_id, body.rating)