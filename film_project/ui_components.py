import streamlit as st
import requests
import base64
import concurrent.futures
from api_client import api_get

def fetch_single_image(url):
    if not url or not url.startswith("http"):
        return None
    try:
        r = requests.get(url, timeout=10.0)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None

@st.cache_data(show_spinner=False, ttl=60) 
def get_images_parallel(urls):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(fetch_single_image, urls))


@st.dialog("О фильме")
def show_movie_details(movie_id, because=None):
    try:
        details = api_get(f"/movies/{movie_id}")

        if because:
            st.info(f"💡 Похоже на: **{because}**")
            
        st.write(f"**Название:** {details['title']}")
        st.write(f"**Год:** {details.get('year') or 'Неизвестно'}")
        st.write(f"**Жанры:** {(details.get('genres') or '').replace('|', ', ')}")
        st.write(f"**Рейтинг:** ★ {details.get('avg_rating', 0):.1f} ({details.get('count_rating', 0)} оценок)")
        st.divider()
        st.write(details.get("overview") or "Описание отсутствует.")
    except Exception:
        st.error("Не удалось загрузить описание фильма.")

def card(m, img_bytes, key, rate_cb, extra_label=None):
    if img_bytes:
        b64 = base64.b64encode(img_bytes).decode()
        img_html = f'<img src="data:image/jpeg;base64,{b64}" style="width:100%; height:100%; object-fit:cover;">'
    else:
        img_html = '<div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; color:#555;">Нет постера</div>'

    extra_html = ""
    if extra_label and extra_label in m:
        extra_html += f'<div style="font-size:0.85em; color:#888; margin-top:4px;">{extra_label}: {m[extra_label]:.2f}</div>'

    html_code = f'<div style="display:flex; flex-direction:column; margin-bottom:10px;"><div style="width:100%; aspect-ratio:2/3; border-radius:10px; overflow:hidden; box-shadow:0 4px 10px rgba(0,0,0,0.5);">{img_html}</div><div style="height:110px; padding-top:10px; overflow:hidden;"><div style="font-weight:600; font-size:1.05em; line-height:1.2; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">{m["title"]}</div><div style="color:#f5b301; font-size:0.9em; margin-top:4px;">★ {m["avg_rating"]:.1f} <span style="color:#888;">({m["count_rating"]} оценок)</span></div>{extra_html}</div></div>'
    
    st.markdown(html_code, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("О фильме", key=f"btn_info_{key}", use_container_width=True):
            show_movie_details(m["movie_id"], m.get("because"))
    with col2:
        if st.button("Оценить", key=f"btn_rate_{key}", use_container_width=True):
            st.session_state[f"show_rate_{key}"] = not st.session_state.get(f"show_rate_{key}", False)

    if st.session_state.get(f"show_rate_{key}", False):
        st.select_slider("Оценка", options=[x / 2 for x in range(1, 11)], value=4.0, key=f"sl_{key}", label_visibility="collapsed")
        st.button("Сохранить", key=f"btn_save_{key}", on_click=rate_cb, args=(key, m["movie_id"]), use_container_width=True, type="primary")

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