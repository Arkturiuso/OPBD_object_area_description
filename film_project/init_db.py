import argparse
import glob
import os
import pandas as pd
import pymysql
from sqlalchemy import create_engine

from db_config import DB_NAME, connect_kwargs, engine_url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()

    print("Чтение CSV...")
    movies = pd.read_csv(os.path.join(a.data_dir, "movies.csv"))
    photo = pd.read_csv(os.path.join(a.data_dir, "photo.csv"))
    rfile = sorted(glob.glob(os.path.join(a.data_dir, "rating*.csv")))[0]
    ratings = pd.read_csv(rfile)

    if "overview" not in photo: photo["overview"] = ""
    movies = movies.merge(photo[["movieId", "poster_url", "overview"]], on="movieId", how="left")
    movies["poster_url"] = movies["poster_url"].fillna("")
    movies["overview"] = movies["overview"].fillna("")
    movies["genres"] = movies["genres"].replace("(no genres listed)", "").fillna("")

    ru_path = os.path.join(a.data_dir, "movies_ru.csv")
    if os.path.exists(ru_path):
        ru = pd.read_csv(ru_path).rename(columns={"movieId": "movie_id"})
        movies = movies.merge(ru[["movie_id", "title_ru", "overview_ru"]], on="movie_id", how="left")
    else:
        movies["title_ru"] = ""
        movies["overview_ru"] = ""
    movies["title_ru"] = movies["title_ru"].fillna("").astype(str)
    empty = movies["title_ru"].str.strip() == ""
    movies.loc[empty, "title_ru"] = movies.loc[empty, "title"]
    movies["overview_ru"] = movies["overview_ru"].fillna("")

    movies["year"] = movies["year"].astype("Int64")
    movies["tmdbId"] = movies["tmdbId"].astype("Int64")
    movies = movies.rename(columns={"movieId": "movie_id", "imdbId": "imdb_id", "tmdbId": "tmdb_id"})
    movies = movies[["movie_id", "imdb_id", "tmdb_id", "title", "title_ru", "year",
                     "genres", "poster_url", "overview", "overview_ru"]]

    ratings = ratings.drop_duplicates(["userId", "movieId"]).rename(
        columns={"userId": "user_id", "movieId": "movie_id", "timestamp": "ts"})
    ratings = ratings[ratings.movie_id.isin(movies.movie_id)]
    
    users = pd.DataFrame({"user_id": sorted(ratings.user_id.unique())})
    users["name"] = "Пользователь " + users.user_id.astype(str)

    print("Подключение к MySQL и создание таблиц...")
    con = pymysql.connect(**connect_kwargs())
    cur = con.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
    cur.execute(f"USE {DB_NAME};")

    cur.execute("DROP TABLE IF EXISTS ratings;")
    cur.execute("DROP TABLE IF EXISTS users;")
    cur.execute("DROP TABLE IF EXISTS movies;")

    cur.execute("""
    CREATE TABLE movies (
        movie_id   INT PRIMARY KEY,
        imdb_id    VARCHAR(20),
        tmdb_id    INT,
        title      VARCHAR(255) NOT NULL,
        title_ru   VARCHAR(512),
        year       INT,
        genres     VARCHAR(255) NOT NULL DEFAULT '',
        poster_url TEXT,
        overview   TEXT,
        overview_ru TEXT
    ) CHARACTER SET utf8mb4;
    """)
    cur.execute("""
    CREATE TABLE users (
        user_id    INT PRIMARY KEY AUTO_INCREMENT,
        name       VARCHAR(100) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) CHARACTER SET utf8mb4;
    """)
    cur.execute("""
    CREATE TABLE ratings (
        user_id  INT NOT NULL,
        movie_id INT NOT NULL,
        rating   FLOAT NOT NULL CHECK (rating >= 0.5 AND rating <= 5),
        ts       BIGINT,
        PRIMARY KEY (user_id, movie_id),
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
        FOREIGN KEY (movie_id) REFERENCES movies(movie_id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4;
    """)
    con.commit()
    con.close()

    print("Заливка данных (это займет пару минут)...")
    engine = create_engine(engine_url())

    movies.to_sql("movies", engine, if_exists="append", index=False)
    users.to_sql("users", engine, if_exists="append", index=False)
    ratings.to_sql("ratings", engine, if_exists="append", index=False)

    print("Готово! База данных MySQL успешно создана и заполнена.")

if __name__ == "__main__":
    main()