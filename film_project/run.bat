@echo off
chcp 65001 >nul

start "Film API" cmd /k "call C:\Users\Thunder\anaconda3\Scripts\activate.bat C:\Users\Thunder\anaconda3 && conda activate recsys && python -m uvicorn api:app --port 8000"

timeout /t 5 >nul

start "Film App" cmd /k "call C:\Users\Thunder\anaconda3\Scripts\activate.bat C:\Users\Thunder\anaconda3 && conda activate recsys && python -m streamlit run app.py"