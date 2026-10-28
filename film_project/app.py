import streamlit as st
from api_client import get_genres, get_users, api_get, api_post
from ui_components import grid

COLS = 5
st.set_page_config(page_title="Diamond Films", layout="wide")

try: api_get("/health")
except Exception:
    st.error("Не удаётся подключиться к API. Сначала запустите сервер (uvicorn).")
    st.stop()

users = get_users()
user_by_id = {u["user_id"]: u for u in users}

def create_user_cb():
    name = st.session_state.get("new_user_name", "").strip()
    if not name: return
    try:
        u = api_post("/users", {"name": name})
        get_users.clear()
        st.session_state["user_id"] = u["user_id"]
        st.session_state["new_user_name"] = ""
    except Exception as e: 
        st.error(str(e))

def rate_cb(key, movie_id):
    rating = st.session_state[f"sl_{key}"]
    try:
        api_post("/ratings", {"user_id": st.session_state["user_id"], "movie_id": movie_id, "rating": rating})
        get_users.clear()
        st.toast(f"Оценка {rating} сохранена")
    except Exception as e: 
        st.toast(f"Ошибка: {e}")

with st.sidebar:
    st.markdown("Рекомендательная система книг")
    page = st.radio("Раздел", ["Популярные", "Поиск и оценка", "Похожие фильмы", "Для меня", "Мои оценки"])
    st.divider()
    
    uid = st.selectbox("Пользователь", [u["user_id"] for u in users], key="user_id", format_func=lambda i: f"{user_by_id[i]['name']} · {user_by_id[i]['ratings_count']} оц.")
    
    with st.expander("Новый пользователь"):
        st.text_input("Имя", key="new_user_name")
        st.button("Создать", on_click=create_user_cb)
    
    if st.button("Очистить кэш картинок"):
        st.cache_data.clear()
        st.rerun()

if page == "Популярные":
    genre = st.selectbox("Жанр", ["Все жанры"] + get_genres())
    grid(api_get("/popular", n=10, genre=None if genre == "Все жанры" else genre), "pop", None, rate_cb, COLS)

elif page == "Поиск и оценка":
    q = st.text_input("Название фильма")
    if q.strip(): 
        grid(api_get("/movies/search", q=q, n=15), "search", None, rate_cb, COLS)

elif page == "Похожие фильмы":
    q = st.text_input("Название фильма")
    if q.strip():
        found = api_get("/movies/search", q=q, n=5)
        if found:
            mid = st.selectbox("Выберите фильм", [m["movie_id"] for m in found], format_func=lambda i: next(m["title"] for m in found if m["movie_id"] == i))
            method = st.radio("Способ", ["По оценкам", "По контенту"], horizontal=True)
            path = "cf" if method == "По оценкам" else "content"
            
            try:
                res = api_get(f"/movies/{mid}/similar/{path}", n=10)
                grid(res, f"sim{path}", "similarity", rate_cb, COLS)
            except RuntimeError as e:
                st.warning(f"⚠️ {e}")
                if path == "cf":
                    st.info("Совет: переключите способ на 'По контенту', этот алгоритм не требует оценок других людей.")

elif page == "Для меня":
    try:
        res = api_get(f"/users/{uid}/recommendations", n=10)
        if res["method"] == "popular":
            st.info("У вас пока нет оценок, показаны популярные фильмы. Оцените что-нибудь!")
        grid(res["items"], "me", "score" if res["method"] != "popular" else None, rate_cb, COLS)
    except RuntimeError as e:
        st.warning(str(e))

elif page == "Мои оценки":
    rated = api_get(f"/users/{uid}/ratings")
    if rated: 
        grid(rated[:20], "mine", None, rate_cb, COLS)
    else: 
        st.info("Вы ещё ничего не оценили.")