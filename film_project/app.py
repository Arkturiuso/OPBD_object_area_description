import streamlit as st
from api_client import get_genres, get_users, api_get, api_post
from ui_components import grid, movie_title

COLS = 5
st.set_page_config(page_title="Diamond Films", layout="wide")

try:
    api_get("/health")
except Exception:
    st.error("Не удаётся подключиться к API. Сначала запустите сервер (uvicorn).")
    st.stop()

users = get_users()
user_by_id = {u["user_id"]: u for u in users}
genres = get_genres()
genre_names = {g["key"]: g["name"] for g in genres}


def create_user_cb():
    name = st.session_state.get("new_user_name", "").strip()
    if not name:
        return
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
        api_post("/ratings", {
            "user_id": st.session_state["user_id"],
            "movie_id": movie_id,
            "rating": rating,
        })
        get_users.clear()
        st.toast(f"Оценка {rating} сохранена")
    except Exception as e:
        st.toast(f"Ошибка: {e}")


with st.sidebar:
    st.markdown("### Рекомендательная система фильмов")
    page = st.radio(
        "Раздел",
        ["Популярные", "Поиск и оценка", "Похожие фильмы", "Для меня", "Мои оценки"],
    )
    st.divider()

    uid = st.selectbox(
        "Пользователь",
        [u["user_id"] for u in users],
        key="user_id",
        format_func=lambda i: f"{user_by_id[i]['name']} · {user_by_id[i]['ratings_count']} оц.",
    )

    with st.expander("Новый пользователь"):
        st.text_input("Имя", key="new_user_name")
        st.button("Создать", on_click=create_user_cb, use_container_width=True)

    if st.button("Очистить кэш картинок", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if page == "Популярные":
    genre_options = ["__all__"] + [g["key"] for g in genres]
    genre = st.selectbox(
        "Жанр",
        genre_options,
        format_func=lambda key: "Все жанры" if key == "__all__" else genre_names.get(key, key),
    )
    try:
        items = api_get("/popular", n=10, genre=None if genre == "__all__" else genre)
        grid(items, "pop", None, rate_cb, COLS)
    except Exception as e:
        st.error(f"Не удалось загрузить популярные фильмы: {e}")

elif page == "Поиск и оценка":
    q = st.text_input("Название фильма", placeholder="Например: Матрица или Matrix")
    if q.strip():
        try:
            grid(api_get("/movies/search", q=q, n=15), "search", None, rate_cb, COLS)
        except Exception as e:
            st.error(f"Ошибка поиска: {e}")

elif page == "Похожие фильмы":
    q = st.text_input("Название фильма", placeholder="Например: Матрица или Matrix")
    if q.strip():
        try:
            found = api_get("/movies/search", q=q, n=5)
        except Exception as e:
            st.error(f"Ошибка поиска: {e}")
            found = []
        if found:
            mid = st.selectbox(
                "Выберите фильм",
                [m["movie_id"] for m in found],
                format_func=lambda i: next(movie_title(m) for m in found if m["movie_id"] == i),
            )
            method = st.radio("Способ", ["По оценкам", "По контенту"], horizontal=True)
            path = "cf" if method == "По оценкам" else "content"
            try:
                res = api_get(f"/movies/{mid}/similar/{path}", n=10)
                grid(res, f"sim{path}", "similarity", rate_cb, COLS)
            except RuntimeError as e:
                st.warning(f"Внимание: {e}")
                if path == "cf":
                    st.info("Совет: переключите способ на «По контенту» - он не требует оценок других людей.")
            except Exception as e:
                st.error(f"Ошибка: {e}")

elif page == "Для меня":
    method_label = st.radio("Метод", ["По контенту", "Гибрид"], horizontal=True)
    method = "content" if method_label == "По контенту" else "hybrid"
    alpha = 0.7
    if method == "hybrid":
        alpha = st.slider(
            "Вес коллаборативной фильтрации (α)", 0.0, 1.0, 0.7, 0.05,
            help="1.0 - только оценки похожих пользователей, 0.0 - только описание фильмов",
        )
    try:
        res = api_get(f"/users/{uid}/recommendations", n=10, method=method, alpha=alpha)
        if res["method"] == "popular":
            st.info("У вас пока нет оценок, показаны популярные фильмы. Оцените что-нибудь!")
        elif res["method"] == "hybrid":
            st.caption(f"Гибрид: {alpha:.0%} CF + {1 - alpha:.0%} контент")
        else:
            st.caption("Контентные рекомендации: TF-IDF по описанию, жанрам и десятилетию")
        grid(res["items"], "me", "score" if res["method"] != "popular" else None, rate_cb, COLS)
    except RuntimeError as e:
        st.warning(str(e))
    except Exception as e:
        st.error(f"Ошибка: {e}")

elif page == "Мои оценки":
    try:
        rated = api_get(f"/users/{uid}/ratings")
    except Exception as e:
        st.error(f"Ошибка: {e}")
        rated = []
    if rated:
        st.caption(f"Всего оценок: {len(rated)}. Показаны последние 20.")
        grid(rated[:20], "mine", "user_rating", rate_cb, COLS)
    else:
        st.info("Вы ещё ничего не оценили.")
