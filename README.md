# Next Fit | Análise de reuniões

## Instalação Windows
1. Extraia o ZIP para uma pasta definitiva. Se já usa a versão anterior, **copie a pasta `data` antiga** para esta nova pasta, com o aplicativo antigo fechado, para manter o banco de dados e sua senha.
2. Instale Python 3.12, se necessário: `winget install Python.Python.3.12`.
3. Execute `INSTALAR_TUDO.bat`. Ele cria o ambiente Python e instala as dependências da aplicação e da transcrição.
4. Execute `INICIAR_WINDOWS.bat`, acesse `http://127.0.0.1:8000`, e faça login com usuário `admin` e senha de `data/.admin_password`.
5. Dentro do perfil de um liderado, selecione o MP4/MP3, escolha entre **Rápido**, **Equilibrado** ou **Preciso** e clique em **Enviar e transcrever**. O andamento é atualizado automaticamente. Ao concluir, baixe o TXT ou o Markdown preparado para enviar ao seu Projeto no GPT.

## Observações
- O primeiro processamento baixa o modelo Whisper pela internet; depois ele fica carregado e é reutilizado nas próximas transcrições. Os perfis Rápido e Equilibrado usam `small`; o Preciso usa `medium` e exige mais memória e tempo.
- O upload pelo navegador é transcrito diretamente do arquivo de mídia, sem criar um WAV intermediário. A fila retoma automaticamente trabalhos interrompidos quando o aplicativo reinicia e o arquivo original ainda está disponível.
- Esta versão **não identifica falantes** e **não analisa com IA automaticamente**; transcreve e permite registrar análises manualmente.
- O upload direto usa o armazenamento temporário do computador; não interrompa o aplicativo enquanto transcreve. No uso local não há limite fixo de tamanho, mas é necessário ter espaço livre para o arquivo enviado e para o modelo. Para impor um teto, configure `MAX_UPLOAD_MB` com um valor maior que zero.
- Os arquivos enviados pelo navegador são excluídos depois de transcritos. Se ocorrer um erro, o original é mantido temporariamente para permitir uma nova tentativa. O texto fica no banco SQLite em `data/historico.db`.
- Faça backups da pasta `data`. Dados de reuniões podem ser sensíveis: avise participantes, limite o acesso e siga a LGPD.

## Railway
- O Dockerfile padrão instala somente as dependências web para reduzir custo e tamanho. A transcrição direta **não vem habilitada no Railway**; é preciso incluir `requirements-local.txt` na imagem, habilitar `TRANSCRIBE_ENABLED=1` e provisionar recursos suficientes para o modelo.
- No painel do serviço, configure `ADMIN_PASSWORD` (mínimo de 12 caracteres), `APP_SECRET` (mínimo de 32 caracteres), `DATA_DIR=/app/data`, `TRANSCRIBE_ENABLED=0` e `MAX_UPLOAD_MB=600`. Gere valores aleatórios com `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- Marque `ADMIN_PASSWORD` e `APP_SECRET` como variáveis seladas no Railway. Nunca coloque os valores no código, no `.env.example` ou no GitHub.
- Crie um volume persistente montado exatamente em `/app/data`. Sem o volume, banco e configurações serão perdidos em um novo deploy.
- Use apenas uma réplica, pois esta versão usa SQLite. Se configurar domínio próprio, defina `ALLOWED_HOSTS` com os domínios permitidos separados por vírgula.
- Para primeira etapa, recomenda-se rodar a transcrição localmente.

## Publicação segura no Git
- O `.gitignore` bloqueia `data/`, `.env`, ambientes virtuais, bancos, mídias, transcrições, logs e caches. Esses arquivos contêm dados locais ou podem conter informações sensíveis.
- Antes do primeiro commit, execute `git status --short --ignored` e confirme que `data/` e `.venv/` aparecem como ignorados.
- Não use `git add -f` nessas pastas. Se um segredo já tiver sido enviado ao GitHub, removê-lo em um commit posterior não basta: troque imediatamente o segredo e remova-o também do histórico.
- O arquivo `.env.example` contém somente os nomes das variáveis e pode ser versionado. Um `.env` real nunca deve ser versionado.
