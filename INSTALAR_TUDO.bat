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
where ffmpeg >nul 2>&1
if errorlevel 1 (
 echo FFmpeg nao encontrado; tentando instalar...
 winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
 echo Se foi instalado agora, FECHE este terminal e execute INSTALAR_TUDO.bat novamente.
 pause
 exit /b 1
)
for %%d in (Entrada Convertidos Transcricoes data) do if not exist "%%d" mkdir "%%d"
echo Registrando Agendador de Tarefas...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0CONFIGURAR_AGENDADOR.ps1"
if errorlevel 1 (echo Nao foi possivel registrar a tarefa. & pause & exit /b 1)
echo.
echo CONFIGURACAO CONCLUIDA.
echo Pasta Entrada: %~dp0Entrada
echo Pasta Convertidos: %~dp0Convertidos
echo Pasta Transcricoes: %~dp0Transcricoes
echo Usuario de acesso: admin
echo Inicie a aplicacao em INICIAR_WINDOWS.bat
echo.
pause
