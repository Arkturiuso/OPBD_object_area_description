import argparse
import glob
import os
import pandas as pd
import pymysql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

MYSQL_USER = "root"
MYSQL_PASS = "1234" 
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
DB_NAME = "films_db"

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
    movies["year"] = movies["year"].astype("Int64")
    movies["tmdbId"] = movies["tmdbId"].astype("Int64")
    movies = movies.rename(columns={"movieId": "movie_id", "imdbId": "imdb_id", "tmdbId": "tmdb_id"})
    movies = movies[["movie_id", "imdb_id", "tmdb_id", "title", "year", "genres", "poster_url", "overview"]]

    ratings = ratings.drop_duplicates(["userId", "movieId"]).rename(
        columns={"userId": "user_id", "movieId": "movie_id", "timestamp": "ts"})
    ratings = ratings[ratings.movie_id.isin(movies.movie_id)]
    
    users = pd.DataFrame({"user_id": sorted(ratings.user_id.unique())})
    users["name"] = "Пользователь " + users.user_id.astype(str)

    print("Подключение к MySQL и создание таблиц...")
    con = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS, port=MYSQL_PORT)
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
        year       INT,
        genres     VARCHAR(255) NOT NULL DEFAULT '',
        poster_url TEXT,
        overview   TEXT
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
    url = URL.create("mysql+pymysql", username=MYSQL_USER, password=MYSQL_PASS, host=MYSQL_HOST, port=MYSQL_PORT, database=DB_NAME, query={"charset": "utf8mb4"})
    engine = create_engine(url)
    
    movies.to_sql("movies", engine, if_exists="append", index=False)
    users.to_sql("users", engine, if_exists="append", index=False)
    ratings.to_sql("ratings", engine, if_exists="append", index=False)
    
    print("Готово! База данных MySQL успешно создана и заполнена.")

if __name__ == "__main__":
    main()