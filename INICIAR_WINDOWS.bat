@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Ambiente ainda nao preparado. Execute INSTALAR_TUDO.bat primeiro.
 pause
 exit /b 1
)
set "TRANSCRIBE_ENABLED=1"
echo Aplicacao: http://127.0.0.1:8000
echo Usuario: admin
if exist data\.admin_password (echo Senha inicial: & type data\.admin_password)
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
