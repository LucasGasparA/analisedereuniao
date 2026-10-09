@echo off
setlocal
cd /d "%~dp0"
echo ==== Next Fit - Instalador completo ====
where py >nul 2>&1
if errorlevel 1 (
 echo Python nao encontrado. Instale Python 3.12 e execute novamente:
 echo winget install Python.Python.3.12
 pause
 exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
 py -3.12 -m venv .venv
 if errorlevel 1 (echo Falha no Python 3.12 & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-local.txt
if errorlevel 1 (echo Falha ao instalar bibliotecas. & pause & exit /b 1)
if not exist "data" mkdir "data"
echo.
echo CONFIGURACAO CONCLUIDA.
echo Usuario de acesso: admin
echo Inicie a aplicacao em INICIAR_WINDOWS.bat
echo.
pause
