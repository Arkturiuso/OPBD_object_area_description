"""Перевод фильмов на русский: названия из Wikidata, описания из ru.wikipedia,
остальное - машинный перевод. Результат: data/movies_ru.csv.

Примеры:
    python translate_data.py                 # собрать переводы
    python translate_data.py --update-db     # собрать и обновить MySQL
    python translate_data.py --apply-only    # только обновить MySQL из CSV
"""

import argparse
import hashlib
import json
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import pymysql
import requests

from db_config import DB_NAME, connect_kwargs

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
WIKIDATA_URL = "https://query.wikidata.org/sparql"
WIKIPEDIA_URL = "https://ru.wikipedia.org/w/api.php"
GOOGLE_CHROME_URL = "https://clients5.google.com/translate_a/t"
GOOGLE_GTX_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"

USER_AGENT = "FilmRecSysEdu/1.0 (student project; offline dataset translation)"

YEAR_RE = re.compile(r"\s*\((\d{4})\)\s*$")
LEADING_ARTICLE_RE = re.compile(r"^(.*),\s*(The|A|An)\s*$", re.IGNORECASE)
DISAMBIG_RE = re.compile(r"\((?:фильм|мультфильм|телефильм)[^)]*\)\s*$", re.IGNORECASE)

_local = threading.local()
_rate_lock = threading.Lock()
_next_request = [0.0]
REQUEST_INTERVAL = 0.25  # не чаще ~4 запросов/сек суммарно


def session() -> requests.Session:
    if not hasattr(_local, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT})
        _local.session = s
    return _local.session


def wait_for_slot():
    with _rate_lock:
        now = time.time()
        delay = max(0.0, _next_request[0] - now)
        _next_request[0] = max(now, _next_request[0]) + REQUEST_INTERVAL
    if delay:
        time.sleep(delay)


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    # На Windows антивирус/индексатор иногда держит файл открытым.
    for attempt in range(6):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    path.write_text(payload, encoding="utf-8")
    try:
        tmp.unlink()
    except OSError:
        pass


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def clean_english_title(title: str) -> str:
    title = YEAR_RE.sub("", str(title)).strip()
    m = LEADING_ARTICLE_RE.match(title)
    if m:
        title = f"{m.group(2)} {m.group(1)}"
    return title.strip()


def clean_russian_title(title: str) -> str:
    title = str(title).strip()
    title = DISAMBIG_RE.sub("", title).strip()
    return title


def clean_overview(text: str, limit: int = 1800) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) < 80:
        return ""
    low = text[:350].lower()
    if any(x in low for x in (
        "может означать",
        "неоднозначност",
        "страница значений",
        "список значений",
        "это список",
    )):
        return ""
    if len(text) > limit:
        cut = text[:limit]
        pos = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = (cut[:pos + 1] if pos > 600 else cut.rstrip()) + "…"
    return text


def fetch_wikidata(imdb_ids, cache_path: Path):
    cache = load_json(cache_path, {})
    missing = [x for x in imdb_ids if x not in cache]
    print(f"Wikidata: в кеше {len(imdb_ids) - len(missing)}, запрашиваем {len(missing)}")

    done = 0
    for batch in chunks(missing, 200):
        values = " ".join(f'"{x}"' for x in batch)
        query = f"""
        SELECT ?imdb ?label ?article WHERE {{
          VALUES ?imdb {{ {values} }}
          ?film wdt:P345 ?imdb .
          OPTIONAL {{ ?film rdfs:label ?label FILTER(lang(?label)="ru") }}
          OPTIONAL {{ ?article schema:about ?film ;
                              schema:isPartOf <https://ru.wikipedia.org/> }}
        }}
        """
        data = None
        for attempt in range(4):
            try:
                wait_for_slot()
                r = session().post(
                    WIKIDATA_URL,
                    data={"query": query, "format": "json"},
                    timeout=90,
                )
                if r.status_code == 200:
                    data = r.json()["results"]["bindings"]
                    break
                print(f"  Wikidata {r.status_code}, повтор {attempt + 1}/4")
            except Exception as e:
                print(f"  Wikidata ошибка: {type(e).__name__}, повтор {attempt + 1}/4")
            time.sleep(2 * (attempt + 1))

        for imdb in batch:
            cache.setdefault(imdb, {})
        if data is not None:
            for row in data:
                imdb = row["imdb"]["value"]
                item = cache.setdefault(imdb, {})
                if "label" in row:
                    item["label"] = row["label"]["value"]
                if "article" in row:
                    url = row["article"]["value"]
                    if "/wiki/" in url:
                        item["article"] = urllib.parse.unquote(
                            url.split("/wiki/", 1)[1]
                        ).replace("_", " ")
        done += len(batch)
        save_json(cache_path, cache)
        print(f"  обработано {done}/{len(missing)}")

    return cache


def fetch_wikipedia(article_titles, cache_path: Path):
    cache = load_json(cache_path, {})
    missing = sorted({t for t in article_titles if t and t not in cache})
    print(f"Википедия: в кеше {len(set(article_titles)) - len(missing)}, запрашиваем {len(missing)}")

    for n, batch in enumerate(chunks(missing, 20), 1):
        params = {
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "exlimit": "max",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
            "titles": "|".join(batch),
        }
        payload = None
        for attempt in range(4):
            try:
                wait_for_slot()
                r = session().get(WIKIPEDIA_URL, params=params, timeout=60)
                if r.status_code == 200:
                    payload = r.json()
                    break
                print(f"  Wikipedia {r.status_code}, повтор {attempt + 1}/4")
            except Exception as e:
                print(f"  Wikipedia ошибка: {type(e).__name__}, повтор {attempt + 1}/4")
            time.sleep(2 * (attempt + 1))

        if payload is None:
            continue

        query = payload.get("query", {})
        norm = {x["from"]: x["to"] for x in query.get("normalized", [])}
        redirects = {x["from"]: x["to"] for x in query.get("redirects", [])}

        def resolve(title):
            title = norm.get(title, title)
            seen = set()
            while title in redirects and title not in seen:
                seen.add(title)
                title = redirects[title]
            return title

        requested = {resolve(t): t for t in batch}
        for page in query.get("pages", []):
            original = requested.get(page.get("title"))
            if original:
                cache[original] = page.get("extract", "") or ""

        for t in batch:
            cache.setdefault(t, "")
        save_json(cache_path, cache)
        if n % 20 == 0:
            print(f"  обработано батчей: {n}")

    return cache


def parse_google_payload(payload):
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if isinstance(first, str):
        return first
    if isinstance(first, list):
        parts = []
        for part in first:
            if isinstance(part, list) and part and isinstance(part[0], str):
                parts.append(part[0])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts) or None
    return None


def split_text(text: str, limit: int = 1500):
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out, current = [], ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= limit:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                out.append(current)
            current = sentence
    if current:
        out.append(current)
    return out


def machine_translate(text: str):
    text = str(text or "").strip()
    if not text:
        return ""

    parts = split_text(text)
    translated = []
    for part in parts:
        result = _translate_part(part)
        if not result:
            return None
        translated.append(result)
    return " ".join(translated).strip()


def _translate_part(text: str):
    wait_for_slot()
    s = session()

    # 1) Chrome-словарь Google - быстро и без ключа.
    try:
        r = s.get(
            GOOGLE_CHROME_URL,
            params={"client": "dict-chrome-ex", "sl": "en", "tl": "ru", "q": text},
            timeout=40,
        )
        if r.status_code == 200:
            result = parse_google_payload(r.json())
            if result:
                return result
    except Exception:
        pass

    # 2) Обычный Google Translate (POST).
    try:
        wait_for_slot()
        r = s.post(
            GOOGLE_GTX_URL,
            data={"client": "gtx", "sl": "en", "tl": "ru", "dt": "t", "q": text},
            timeout=40,
        )
        if r.status_code == 200:
            result = parse_google_payload(r.json())
            if result:
                return result
    except Exception:
        pass

    # 3) MyMemory - только для коротких текстов.
    if len(text) <= 500:
        try:
            wait_for_slot()
            r = s.get(
                MYMEMORY_URL,
                params={"q": text, "langpair": "en|ru"},
                timeout=40,
            )
            if r.status_code == 200:
                data = r.json()
                result = (data.get("responseData") or {}).get("translatedText")
                if result and "MYMEMORY WARNING" not in result.upper():
                    return result
        except Exception:
            pass

    return None


def chrome_batch(texts):
    """Google Chrome-эндпоинт умеет переводить несколько текстов за один запрос."""
    params = [("client", "dict-chrome-ex"), ("sl", "en"), ("tl", "ru")]
    params += [("q", t) for t in texts]
    wait_for_slot()
    try:
        r = session().get(GOOGLE_CHROME_URL, params=params, timeout=60)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or len(data) != len(texts):
            return None
        return [x if isinstance(x, str) and x.strip() else None for x in data]
    except Exception:
        return None


def translate_many(items: dict, cache, cache_path: Path, prefix: str,
                   batch_size: int = 25, max_chars: int = 5000):
    """items: ключ -> исходный текст. Возвращает ключ -> перевод."""
    pending = {k: v for k, v in items.items() if f"{prefix}:{k}" not in cache}
    # Уже переведённое при предыдущем запуске тоже должно попасть в результат.
    results = {k: cache[f"{prefix}:{k}"] for k in items
               if f"{prefix}:{k}" in cache and cache[f"{prefix}:{k}"]}
    print(f"Машинный перевод ({prefix}): нужно {len(pending)} из {len(items)}")

    keys = list(pending)

    # Очень длинные тексты переводим по одному: они сами делятся на части.
    long_keys = [k for k in keys if len(pending[k]) > 1500]
    batch_keys = [k for k in keys if k not in long_keys]

    done = 0
    for key in long_keys:
        result = machine_translate(pending[key])
        if result:
            cache[f"{prefix}:{key}"] = result
            results[key] = result
        done += 1
        if done % 25 == 0:
            save_json(cache_path, cache)
            print(f"  переведено {done}/{len(keys)}")

    batches, current, current_len = [], [], 0
    for key in batch_keys:
        text = pending[key]
        if current and (len(current) >= batch_size or current_len + len(text) > max_chars):
            batches.append(current)
            current, current_len = [], 0
        current.append(key)
        current_len += len(text)
    if current:
        batches.append(current)

    for n, batch in enumerate(batches, 1):
        texts = [pending[k] for k in batch]
        translated = chrome_batch(texts)

        if translated is None:
            translated = [machine_translate(t) for t in texts]

        for key, result in zip(batch, translated):
            if result:
                cache[f"{prefix}:{key}"] = result
                results[key] = result

        done += len(batch)
        if n % 5 == 0:
            save_json(cache_path, cache)
        if done % 100 < len(batch):
            print(f"  переведено {done}/{len(keys)}")

    save_json(cache_path, cache)
    return results


def build_translations(data_dir: Path, use_wiki: bool, use_mt: bool,
                       batch_size: int = 25):
    movies = pd.read_csv(data_dir / "movies.csv")
    photo = pd.read_csv(data_dir / "photo.csv")

    if "overview" not in photo:
        photo["overview"] = ""
    photo["overview"] = photo["overview"].fillna("")
    if "overview" in movies.columns:
        movies = movies.drop(columns=["overview"])
    movies = movies.merge(photo[["movieId", "overview"]], on="movieId", how="left")
    movies["overview"] = movies["overview"].fillna("")
    movies["title"] = movies["title"].fillna("")
    movies["genres"] = movies["genres"].replace("(no genres listed)", "").fillna("")

    imdb_ids = [str(x) for x in movies["imdbId"].dropna().unique()]
    wd = fetch_wikidata(imdb_ids, data_dir / "wikidata_ru.json")

    wiki_path = data_dir / "wiki_extracts_ru.json"
    wiki = load_json(wiki_path, {})
    if use_wiki:
        article_titles = [wd.get(x, {}).get("article", "") for x in imdb_ids]
        article_titles = [t for t in article_titles if t]
        wiki = fetch_wikipedia(article_titles, wiki_path)

    title_by_imdb = {}
    overview_by_imdb = {}
    for imdb in imdb_ids:
        item = wd.get(imdb, {})
        title_by_imdb[imdb] = clean_russian_title(item.get("label", "")) if item.get("label") else ""
        overview_by_imdb[imdb] = clean_overview(wiki.get(item.get("article", ""), ""))

    # заголовки без официального русского названия
    fallback_titles = set()
    for row in movies.itertuples(index=False):
        imdb = str(getattr(row, "imdbId", ""))
        if not title_by_imdb.get(imdb):
            fallback_titles.add(clean_english_title(row.title))

    # описания без русской статьи
    fallback_overviews = set()
    overview_map = {}
    for row in movies.itertuples(index=False):
        imdb = str(getattr(row, "imdbId", ""))
        if overview_by_imdb.get(imdb):
            continue
        text = str(getattr(row, "overview", "") or "").strip()
        if not text:
            continue
        key = hashlib.md5(text.encode("utf-8")).hexdigest()
        overview_map[key] = text
        fallback_overviews.add(key)

    cache_path = data_dir / "translation_cache.json"
    cache = load_json(cache_path, {})
    title_mt = {}
    overview_mt = {}
    if use_mt:
        title_mt = translate_many(
            {t: t for t in fallback_titles}, cache, cache_path, "t",
            batch_size=50, max_chars=2500,
        )
        overview_mt = translate_many(
            {key: overview_map[key] for key in fallback_overviews},
            cache, cache_path, "o",
            batch_size=batch_size, max_chars=5000,
        )

    rows = []
    n_title_wd = n_title_mt = n_title_en = 0
    n_overview_wiki = n_overview_mt = n_overview_empty = 0

    for row in movies.itertuples(index=False):
        imdb = str(getattr(row, "imdbId", ""))
        title_ru = title_by_imdb.get(imdb, "")
        if title_ru:
            n_title_wd += 1
        else:
            english = clean_english_title(row.title)
            title_ru = title_mt.get(english, "")
            if title_ru:
                n_title_mt += 1
            else:
                title_ru = english
                n_title_en += 1

        overview_ru = overview_by_imdb.get(imdb, "")
        if overview_ru:
            n_overview_wiki += 1
        else:
            english = str(getattr(row, "overview", "") or "").strip()
            key = hashlib.md5(english.encode("utf-8")).hexdigest() if english else ""
            overview_ru = overview_mt.get(key, "") if key else ""
            if overview_ru:
                n_overview_mt += 1
            else:
                n_overview_empty += 1

        rows.append({
            "movieId": int(row.movieId),
            "title_ru": title_ru.strip(),
            "overview_ru": overview_ru.strip(),
        })

    out_path = data_dir / "movies_ru.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nСохранено: {out_path}")
    print(f"Названия: Wikidata {n_title_wd}, машинный перевод {n_title_mt}, без перевода {n_title_en}")
    print(f"Описания: Википедия {n_overview_wiki}, машинный перевод {n_overview_mt}, пусто {n_overview_empty}")
    return out_path


def update_db(csv_path: Path):
    if not csv_path.exists():
        raise SystemExit(f"Нет файла {csv_path}. Сначала запустите перевод.")
    translations = pd.read_csv(csv_path)

    con = pymysql.connect(
        **connect_kwargs(),
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with con.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM movies")
            columns = {row["Field"] for row in cur.fetchall()}
            if "title_ru" not in columns:
                cur.execute("ALTER TABLE movies ADD COLUMN title_ru VARCHAR(512) NULL")
            if "overview_ru" not in columns:
                cur.execute("ALTER TABLE movies ADD COLUMN overview_ru TEXT NULL")

            data = [
                (str(r.title_ru) if pd.notna(r.title_ru) else "",
                 str(r.overview_ru) if pd.notna(r.overview_ru) else "",
                 int(r.movieId))
                for r in translations.itertuples(index=False)
            ]
            cur.executemany(
                "UPDATE movies SET title_ru=%s, overview_ru=%s WHERE movie_id=%s",
                data,
            )
        con.commit()
        print(f"Обновлено строк в MySQL: {len(data)}")
    finally:
        con.close()


def main():
    global REQUEST_INTERVAL
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--batch-size", type=int, default=25,
                        help="сколько описаний переводить за один запрос")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL,
                        help="минимальный интервал между запросами перевода, сек")
    parser.add_argument("--wiki", action="store_true",
                        help="докачать недостающие описания из Википедии (медленно из-за лимитов)")
    parser.add_argument("--no-mt", action="store_true")
    parser.add_argument("--update-db", action="store_true")
    parser.add_argument("--apply-only", action="store_true",
                        help="не переводить, только записать готовый movies_ru.csv в MySQL")
    args = parser.parse_args()

    REQUEST_INTERVAL = args.interval
    data_dir = Path(args.data_dir)
    csv_path = data_dir / "movies_ru.csv"

    if not args.apply_only:
        csv_path = build_translations(
            data_dir,
            use_wiki=args.wiki,
            use_mt=not args.no_mt,
            batch_size=args.batch_size,
        )

    if args.update_db or args.apply_only:
        update_db(csv_path)


if __name__ == "__main__":
    main()
