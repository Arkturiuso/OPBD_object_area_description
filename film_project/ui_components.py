import base64
import concurrent.futures
import hashlib
import html
import os
import ssl
import threading

import requests
import streamlit as st
from requests.adapters import HTTPAdapter

from api_client import api_get

# TMDB в России открывается только через VPN, поэтому картинки берём через
# публичный кэш-прокси wsrv.nl. Скачанные постеры кладём на диск, чтобы
# не запрашивать их заново при каждом перезапуске приложения.
POSTER_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "poster_cache")
TMDB_HOST = "image.tmdb.org"
IMAGE_PROXIES = (
    "https://wsrv.nl/?url={url}",
    "https://images.weserv.nl/?url={url}",
)

_direct_tmdb = None
_probe_lock = threading.Lock()

_poster_tls = ssl.create_default_context()
# OpenSSL 3.6 в этом окружении зависает на TLS 1.3 при подключении к wsrv.nl.
_poster_tls.maximum_version = ssl.TLSVersion.TLSv1_2


class _TLS12Adapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs["ssl_context"] = _poster_tls
        super().init_poolmanager(connections, maxsize, block, **kwargs)


_poster_session = requests.Session()
_poster_session.mount("https://", _TLS12Adapter(pool_maxsize=16))
# Cloudflare у wsrv.nl отдаёт 403 на стандартный User-Agent python-requests.
_poster_session.headers["User-Agent"] = "MovieRecommender/1.0"

EXTRA_LABELS = {
    "similarity": "Сходство",
    "score": "Балл",
    "user_rating": "Ваша оценка",
}


def movie_title(m):
    return m.get("title_ru") or m.get("title") or ""


def _probe_direct(url):
    try:
        return _poster_session.get(url, timeout=3.0).status_code == 200
    except requests.RequestException:
        return False


def _candidates(url):
    """Прямой адрес и прокси; порядок зависит от доступности TMDB."""
    global _direct_tmdb
    if TMDB_HOST not in url:
        return [(url, 10.0)]

    path = url.split("://", 1)[-1]
    proxied = [(template.format(url=path), 10.0) for template in IMAGE_PROXIES]

    if _direct_tmdb is None:
        with _probe_lock:
            if _direct_tmdb is None:
                _direct_tmdb = _probe_direct(url)
    if _direct_tmdb:
        return [(url, 10.0)] + proxied
    return proxied + [(url, 4.0)]


def _cache_file(url):
    return os.path.join(POSTER_CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".jpg")


def _read_cached(url):
    try:
        with open(_cache_file(url), "rb") as f:
            return f.read() or None
    except OSError:
        return None


def _save_cached(url, data):
    try:
        os.makedirs(POSTER_CACHE_DIR, exist_ok=True)
        path = _cache_file(url)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        pass


def fetch_single_image(url):
    if not url or not url.startswith("http"):
        return None

    cached = _read_cached(url)
    if cached:
        return cached

    for candidate, timeout in _candidates(url):
        try:
            r = _poster_session.get(candidate, timeout=timeout)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.content:
            _save_cached(url, r.content)
            return r.content
    return None


@st.cache_data(show_spinner=False, ttl=60)
def get_images_parallel(urls):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        return list(executor.map(fetch_single_image, urls))


@st.dialog("О фильме")
def show_movie_details(movie_id, because=None):
    try:
        details = api_get(f"/movies/{movie_id}")

        if because:
            st.info(f"Похоже на: **{because}**")

        title_ru = details.get("title_ru") or details.get("title") or ""
        st.write(f"**Название:** {title_ru}")
        if details.get("title") and details["title"] != title_ru:
            st.caption(f"Оригинальное название: {details['title']}")

        st.write(f"**Год:** {details.get('year') or 'Неизвестно'}")
        genres = details.get("genres_ru") or (details.get("genres") or "").replace("|", ", ")
        st.write(f"**Жанры:** {genres or 'Не указаны'}")
        st.write(
            f"**Рейтинг:** {details.get('avg_rating', 0):.1f} "
            f"({details.get('count_rating', 0)} оценок)"
        )
        st.divider()
        st.write(
            details.get("overview_ru")
            or details.get("overview")
            or "Описание отсутствует."
        )
    except Exception:
        st.error("Не удалось загрузить описание фильма.")


def card(m, img_bytes, key, rate_cb, extra_label=None):
    if img_bytes:
        b64 = base64.b64encode(img_bytes).decode()
        img_html = (
            f'<img src="data:image/jpeg;base64,{b64}" '
            f'style="width:100%; height:100%; object-fit:cover;">'
        )
    else:
        img_html = (
            '<div style="width:100%; height:100%; display:flex; align-items:center; '
            'justify-content:center; color:#555;">Нет постера</div>'
        )

    title = html.escape(movie_title(m))
    year = m.get("year")
    year_html = (
        f'<span style="color:#888; margin-left:6px;">{int(year)}</span>'
        if year else ""
    )

    extra_html = ""
    if extra_label and extra_label in m and m[extra_label] is not None:
        label = EXTRA_LABELS.get(extra_label, extra_label)
        extra_html = (
            f'<div style="font-size:0.85em; color:#888; margin-top:4px;">'
            f'{label}: {m[extra_label]:.2f}</div>'
        )

    html_code = (
        '<div style="display:flex; flex-direction:column; margin-bottom:10px;">'
        '<div style="width:100%; aspect-ratio:2/3; border-radius:10px; overflow:hidden; '
        'box-shadow:0 4px 10px rgba(0,0,0,0.5);">'
        f'{img_html}</div>'
        '<div style="height:110px; padding-top:10px; overflow:hidden;">'
        f'<div style="font-weight:600; font-size:1.05em; line-height:1.2; '
        f'display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; '
        f'overflow:hidden;">{title}{year_html}</div>'
        f'<div style="color:#f5b301; font-size:0.9em; margin-top:4px;">'
        f'{m["avg_rating"]:.1f} <span style="color:#888;">'
        f'({m["count_rating"]} оценок)</span></div>'
        f'{extra_html}</div></div>'
    )

    st.markdown(html_code, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("О фильме", key=f"btn_info_{key}", use_container_width=True):
            show_movie_details(m["movie_id"], m.get("because"))
    with col2:
        if st.button("Оценить", key=f"btn_rate_{key}", use_container_width=True):
            st.session_state[f"show_rate_{key}"] = not st.session_state.get(f"show_rate_{key}", False)

    if st.session_state.get(f"show_rate_{key}", False):
        default = float(m.get("user_rating") or 4.0)
        st.select_slider(
            "Оценка",
            options=[x / 2 for x in range(1, 11)],
            value=default,
            key=f"sl_{key}",
            label_visibility="collapsed",
        )
        st.button(
            "Сохранить",
            key=f"btn_save_{key}",
            on_click=rate_cb,
            args=(key, m["movie_id"]),
            use_container_width=True,
            type="primary",
        )


def grid(items, section, extra_label, rate_cb, cols=5):
    if not items:
        st.info("Ничего не найдено.")
        return

    urls = [m.get("poster_url", "") for m in items]

    with st.spinner("Загрузка постеров..."):
        images_bytes = get_images_parallel(urls)

    for start in range(0, len(items), cols):
        columns = st.columns(cols, gap="medium")
        for col, m, img_b in zip(columns, items[start:start + cols], images_bytes[start:start + cols]):
            with col:
                card(m, img_b, f"{section}_{m['movie_id']}", rate_cb, extra_label)
