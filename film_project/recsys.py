import re
import threading
import numpy as np
import pandas as pd
import pymysql
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.neighbors import NearestNeighbors
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

MYSQL_USER = "root"
MYSQL_PASS = "1234"
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
DB_NAME = "films_db"

MIN_ITEM_RATINGS = 10
OUT_COLS = ["movie_id", "title", "year", "genres", "poster_url", "avg_rating", "count_rating", "w_score"]

class NotEnoughData(ValueError):
    pass

def _norm(a):
    a = np.asarray(a, dtype=float)
    rng = a.max() - a.min() if len(a) else 0
    return (a - a.min()) / rng if rng > 0 else np.zeros_like(a)

class Recommender:
    def __init__(self, db_path: str = None):
        self._lock = threading.RLock()
        self._dirty = True
        
        url = URL.create("mysql+pymysql", username=MYSQL_USER, password=MYSQL_PASS, host=MYSQL_HOST, port=MYSQL_PORT, database=DB_NAME, query={"charset": "utf8mb4"})
        self.engine = create_engine(url)
        
        with self.engine.connect() as con:
            self.movies = pd.read_sql("SELECT * FROM movies", con)
        self._build_content()

    def _conn(self):
        return pymysql.connect(
            host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS, 
            database=DB_NAME, port=MYSQL_PORT, cursorclass=pymysql.cursors.DictCursor
        )

    def _ensure(self):
        with self._lock:
            if not self._dirty: return
            with self.engine.connect() as con:
                self.ratings = pd.read_sql("SELECT user_id, movie_id, rating FROM ratings", con)
            self._build_cf()
            self._dirty = False

    def _build_content(self):
        genres = self.movies.genres.str.replace("-", "", regex=False).str.replace("|", " ", regex=False).str.lower()
        decade = self.movies.year.apply(lambda y: f"decade{int(y) // 10 * 10}" if pd.notna(y) else "")
        overview = self.movies["overview"].fillna("") if "overview" in self.movies else ""
        txt = overview + " " + (genres + " ") * 3 + decade
        vec = TfidfVectorizer(token_pattern=r"[A-Za-z0-9]{2,}", lowercase=True, stop_words="english", ngram_range=(1, 2), min_df=2, sublinear_tf=True)
        self.X_content = vec.fit_transform(txt)
        self.knn_content = NearestNeighbors(metric="cosine", algorithm="brute").fit(self.X_content)
        self.mpos = {int(m): i for i, m in enumerate(self.movies.movie_id)}

    def _build_cf(self):
        r = self.ratings
        st = r.groupby("movie_id").agg(avg_rating=("rating", "mean"), count_rating=("rating", "count"))
        v, R = st.count_rating, st.avg_rating
        m, C = v.quantile(0.90), R.mean()
        st["w_score"] = (v * R + m * C) / (v + m)
        cat = self.movies.merge(st, left_on="movie_id", right_index=True, how="left")
        cat["count_rating"] = cat.count_rating.fillna(0).astype(int)
        cat["avg_rating"] = cat.avg_rating.fillna(0.0)
        cat["w_score"] = cat.w_score.fillna(0.0)
        self.catalog = cat.set_index("movie_id", drop=False)

        self.user_ids = np.sort(r.user_id.unique())
        self.movie_ids = np.sort(r.movie_id.unique())
        self.uidx = {int(u): i for i, u in enumerate(self.user_ids)}
        self.midx = {int(m_): i for i, m_ in enumerate(self.movie_ids)}
        self.UI = csr_matrix((r.rating.values, (r.user_id.map(self.uidx).values, r.movie_id.map(self.midx).values)), shape=(len(self.user_ids), len(self.movie_ids)))
        self.knn_user = NearestNeighbors(metric="cosine", algorithm="brute").fit(self.UI)

        cnt = np.asarray((self.UI > 0).sum(axis=0)).ravel()
        cols = np.where(cnt >= MIN_ITEM_RATINGS)[0]
        self.item_movie_ids = self.movie_ids[cols]
        self.iidx = {int(m_): i for i, m_ in enumerate(self.item_movie_ids)}
        self.IU = self.UI[:, cols].T.tocsr()
        self.knn_item = NearestNeighbors(metric="cosine", algorithm="brute").fit(self.IU)

    def _out(self, ids, extra=None):
        d = self.catalog.loc[list(ids), OUT_COLS].copy()
        if extra:
            for k, vals in extra.items(): d[k] = list(vals)
        d["year"] = d["year"].astype(object).where(d["year"].notna(), None)
        d["avg_rating"] = d["avg_rating"].round(2)
        d["w_score"] = d["w_score"].round(2)
        recs = d.to_dict("records")
        for rec in recs:
            rec["movie_id"] = int(rec["movie_id"])
            rec["year"] = None if rec["year"] is None else int(rec["year"])
            rec["count_rating"] = int(rec["count_rating"])
            for k in extra or {}: rec[k] = round(float(rec[k]), 3)
        return recs

    def genres(self):
        g = set()
        for s in self.movies.genres: g.update(x for x in s.split("|") if x)
        return sorted(g)

    def get_movie(self, movie_id: int):
        self._ensure()
        if movie_id not in self.catalog.index: raise KeyError(movie_id)
        return self._out([movie_id])[0]

    def search(self, q: str, n: int = 20):
        self._ensure()
        q = (q or "").strip()
        if not q: return self.popular(n)
        mask = self.catalog.title.str.contains(re.escape(q), case=False, na=False)
        ids = self.catalog[mask].sort_values("count_rating", ascending=False).head(n).movie_id.tolist()
        return self._out(ids)

    def popular(self, n: int = 10, genre: str | None = None):
        self._ensure()
        d = self.catalog[self.catalog.count_rating > 0]
        if genre: d = d[d.genres.str.split("|").apply(lambda g: genre in g)]
        return self._out(d.sort_values("w_score", ascending=False).head(n).movie_id.tolist())

    def similar_cf(self, movie_id: int, n: int = 10):
        self._ensure()
        if movie_id not in self.catalog.index: raise KeyError(movie_id)
        if movie_id not in self.iidx: raise NotEnoughData("У фильма слишком мало оценок.")
        dist, ind = self.knn_item.kneighbors(self.IU[self.iidx[movie_id]], n_neighbors=n + 1)
        ids = [int(self.item_movie_ids[j]) for j in ind[0][1:]]
        return self._out(ids, {"similarity": 1 - dist[0][1:]})

    def similar_content(self, movie_id: int, n: int = 10, pool: int = 200, min_votes: int = 20):
        self._ensure()
        if movie_id not in self.mpos: raise KeyError(movie_id)
        dist, ind = self.knn_content.kneighbors(self.X_content[self.mpos[movie_id]], n_neighbors=pool)
        cand = self.catalog.iloc[ind[0]].copy()
        cand["similarity"] = 1 - dist[0]
        cand = cand[(cand.movie_id != movie_id) & (cand.count_rating >= min_votes)]
        cand = cand.sort_values(["similarity", "w_score"], ascending=False).head(n)
        return self._out(cand.movie_id.tolist(), {"similarity": cand.similarity.values})

    def _vector(self, user_ratings: dict):
        vec = np.zeros(len(self.movie_ids))
        for mv, r in user_ratings.items():
            if mv in self.midx: vec[self.midx[mv]] = r
        return vec

    def _cf_scores(self, vec, k=30, shrink=1.0):
        dist, ind = self.knn_user.kneighbors(csr_matrix(vec), n_neighbors=min(k + 1, self.UI.shape[0]))
        sim = 1 - dist[0]
        keep = (sim < 0.9999) & (sim > 0)
        sim, ind = sim[keep], ind[0][keep]
        neigh = self.UI[ind]
        num = neigh.T.dot(sim)
        den = (neigh > 0).T.astype(float).dot(sim)
        return np.asarray(num / (den + shrink)).ravel(), np.asarray(den).ravel()

    def user_ratings(self, user_id: int) -> dict:
        self._ensure()
        d = self.ratings[self.ratings.user_id == user_id]
        return {int(m): float(r) for m, r in zip(d.movie_id, d.rating)}

    def recommend_for_ratings(self, user_ratings: dict, n: int = 10, k: int = 30):
        self._ensure()
        vec = self._vector(user_ratings)
        scores, den = self._cf_scores(vec, k)
        scores[vec > 0] = -1
        scores[den < 0.5] = -1
        order = [i for i in np.argsort(-scores) if scores[i] > 0][:n]
        return self._out([int(self.movie_ids[i]) for i in order], {"score": scores[order]})

    def recommend_user_cf(self, user_id: int, n: int = 10, k: int = 30):
        self._check_user(user_id)
        ur = self.user_ratings(user_id)
        if not ur: raise NotEnoughData("У пользователя ещё нет оценок.")
        return self.recommend_for_ratings(ur, n, k)

    def _liked(self, ur: dict):
        liked = {m: r for m, r in ur.items() if r >= 4 and m in self.mpos}
        if not liked:
            best = sorted((x for x in ur.items() if x[0] in self.mpos), key=lambda x: -x[1])[:5]
            liked = dict(best)
        return liked

    def _content_scores(self, liked: dict):
        rows = [self.mpos[m] for m in liked]
        w = np.array([liked[m] for m in liked], dtype=float)
        profile = np.asarray(self.X_content[rows].T.dot(w)).reshape(1, -1) / w.sum()
        return cosine_similarity(profile, self.X_content).ravel()

    def recommend_by_description(self, user_ratings: dict, n: int = 10, min_votes: int = 5):
        self._ensure()
        liked = self._liked(user_ratings)
        if not liked: return []
        sims = self._content_scores(liked)
        score = pd.Series(sims, index=self.movies.movie_id.values)
        ok = self.catalog.index[(self.catalog.count_rating >= min_votes) & (~self.catalog.index.isin(list(user_ratings)))]
        top = score.reindex(ok).sort_values(ascending=False).head(n)
        liked_ids = list(liked)
        L = self.X_content[[self.mpos[m] for m in liked_ids]]
        because = []
        for mid in top.index:
            j = int(np.argmax(cosine_similarity(self.X_content[self.mpos[mid]], L).ravel()))
            because.append(self.catalog.loc[liked_ids[j], "title"])
        out = self._out(top.index.tolist(), {"score": top.values})
        for rec, bc in zip(out, because): rec["because"] = bc
        return out

    def recommend_personal(self, user_id: int, n: int = 10, method: str = "content", alpha: float = 0.7, k: int = 30, min_votes: int = 10):
        self._check_user(user_id)
        ur = self.user_ratings(user_id)
        if not ur: return {"method": "popular", "items": self.popular(n)}
        if method == "content": return {"method": "content", "items": self.recommend_by_description(ur, n)}
        liked = self._liked(ur)
        cf, _ = self._cf_scores(self._vector(ur), k)
        cf = pd.Series(cf, index=self.movie_ids)
        cand = self.catalog.index[(self.catalog.count_rating >= min_votes) & (~self.catalog.index.isin(list(ur)))]
        cf_c = cf.reindex(cand).fillna(0).values
        content = pd.Series(self._content_scores(liked), index=self.movies.movie_id.values)
        score = alpha * _norm(cf_c) + (1 - alpha) * _norm(content.reindex(cand).values)
        top = np.argsort(-score)[:n]
        return {"method": "hybrid", "items": self._out(cand[top].tolist(), {"score": score[top]})}

    def _check_user(self, user_id: int):
        con = self._conn()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE user_id=%s", (user_id,))
                if cur.fetchone() is None: raise KeyError(user_id)
        finally: con.close()

    def list_users(self):
        con = self._conn()
        try:
            with con.cursor() as cur:
                cur.execute("""SELECT u.user_id, u.name, COUNT(r.movie_id) as cnt FROM users u LEFT JOIN ratings r ON u.user_id = r.user_id GROUP BY u.user_id ORDER BY u.user_id""")
                rows = cur.fetchall()
            return [{"user_id": r["user_id"], "name": r["name"], "ratings_count": r["cnt"]} for r in rows]
        finally: con.close()

    def create_user(self, name: str):
        con = self._conn()
        try:
            with con.cursor() as cur:
                cur.execute("INSERT INTO users(name) VALUES (%s)", (name.strip(),))
                uid = cur.lastrowid
            con.commit()
            return {"user_id": uid, "name": name.strip(), "ratings_count": 0}
        finally: con.close()

    def rated_by_user(self, user_id: int):
        self._check_user(user_id)
        con = self._conn()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT movie_id, rating FROM ratings WHERE user_id=%s ORDER BY ts DESC", (user_id,))
                rows = cur.fetchall()
        finally: con.close()
        
        self._ensure()
        out = []
        for row in rows:
            rec = self._out([row["movie_id"]])[0]
            rec["user_rating"] = row["rating"]
            out.append(rec)
        return out

    def add_rating(self, user_id: int, movie_id: int, rating: float):
        if not (0.5 <= rating <= 5): raise ValueError("Оценка должна быть от 0.5 до 5")
        self._check_user(user_id)
        if movie_id not in self.mpos: raise KeyError(movie_id)
        
        con = self._conn()
        try:
            with con.cursor() as cur:
                # Для MySQL используем ON DUPLICATE KEY UPDATE
                cur.execute("""
                    INSERT INTO ratings(user_id, movie_id, rating, ts) 
                    VALUES (%s, %s, %s, UNIX_TIMESTAMP())
                    ON DUPLICATE KEY UPDATE rating=%s, ts=UNIX_TIMESTAMP()
                """, (user_id, movie_id, float(rating), float(rating)))
            con.commit()
        finally:
            con.close()
            
        with self._lock: self._dirty = True
        return {"user_id": user_id, "movie_id": movie_id, "rating": float(rating)}