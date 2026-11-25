@echo off
start "Aditya-L1 Backend (FastAPI)" cmd /k "python -m uvicorn main:app --port 8000"
timeout /t 5 /nobreak > nul
start "Aditya-L1 Frontend (Streamlit)" cmd /k "python -m streamlit run dashboard.py"