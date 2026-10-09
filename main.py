import os, sqlite3, secrets, hashlib, hmac, json, threading, queue, uuid, time
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager, asynccontextmanager
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('DATA_DIR', str(ROOT/'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA/'historico.db'
UPLOADS = DATA/'uploads'
UPLOADS.mkdir(exist_ok=True)
IS_RAILWAY = bool(os.getenv('RAILWAY_ENVIRONMENT_ID') or os.getenv('RAILWAY_PROJECT_ID'))
TRANSCRIBE_ENABLED = os.getenv('TRANSCRIBE_ENABLED', '0') == '1'
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '0'))
MAX_UPLOAD = MAX_UPLOAD_MB * 1024 * 1024 if MAX_UPLOAD_MB > 0 else None
MEDIA_SUFFIXES = ('.mp4','.mp3','.m4a','.wav','.mov')
TRANSCRIPTION_PROFILES = {
    'rapido': {
        'label':'Rápido',
        'description':'Prioriza velocidade para rascunhos',
        'model':os.getenv('WHISPER_FAST_MODEL','small'),
        'beam_size':1,
    },
    'equilibrado': {
        'label':'Equilibrado',
        'description':'Boa precisão com tempo moderado',
        'model':os.getenv('WHISPER_BALANCED_MODEL','small'),
        'beam_size':5,
    },
    'preciso': {
        'label':'Preciso',
        'description':'Mais qualidade, usando mais tempo e memória',
        'model':os.getenv('WHISPER_PRECISE_MODEL','medium'),
        'beam_size':5,
    },
}
DEFAULT_TRANSCRIPTION_PROFILE = os.getenv('WHISPER_DEFAULT_PROFILE','equilibrado')
if DEFAULT_TRANSCRIPTION_PROFILE not in TRANSCRIPTION_PROFILES:
    DEFAULT_TRANSCRIPTION_PROFILE = 'equilibrado'
TRANSCRIPTION_QUEUE = queue.Queue()
TRANSCRIPTION_WORKER_LOCK = threading.Lock()
TRANSCRIPTION_WORKER_STARTED = False
TRANSCRIPTION_MODEL_LOCK = threading.Lock()
TRANSCRIPTION_MODEL = None
TRANSCRIPTION_MODEL_NAME = None
LOGIN_ATTEMPTS = {}
LOGIN_ATTEMPTS_LOCK = threading.Lock()
LOGIN_LIMIT = 8
LOGIN_WINDOW_SECONDS = 15*60
# Local secrets persist in data dir; Railway must provide explicit secrets.
SECRET_FILE = DATA/'.secret'
if IS_RAILWAY and (not os.getenv('APP_SECRET') or not os.getenv('ADMIN_PASSWORD')):
    raise RuntimeError('Configure APP_SECRET e ADMIN_PASSWORD nas variaveis do Railway')
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_urlsafe(48), encoding='utf-8')
APP_SECRET = os.getenv('APP_SECRET') or SECRET_FILE.read_text(encoding='utf-8').strip()
PASS_FILE = DATA/'.admin_password'
if not os.getenv('ADMIN_PASSWORD') and not PASS_FILE.exists():
    PASS_FILE.write_text(secrets.token_urlsafe(12), encoding='utf-8')
ADMIN_PASS = os.getenv('ADMIN_PASSWORD') or PASS_FILE.read_text(encoding='utf-8').strip()
ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
if IS_RAILWAY and len(APP_SECRET) < 32:
    raise RuntimeError('APP_SECRET deve ter pelo menos 32 caracteres no Railway')
if IS_RAILWAY and len(ADMIN_PASS) < 12:
    raise RuntimeError('ADMIN_PASSWORD deve ter pelo menos 12 caracteres no Railway')

@asynccontextmanager
async def lifespan(_app):
    start_transcription_worker()
    yield

app = FastAPI(title='Next Fit - Análise de reuniões',lifespan=lifespan)
app.mount('/static', StaticFiles(directory=str(ROOT/'static')), name='static')
app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET,
    same_site='lax',
    https_only=IS_RAILWAY,
    max_age=8*60*60
)
allowed_hosts=[host.strip() for host in os.getenv('ALLOWED_HOSTS','').split(',') if host.strip()]
if allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
tpl = Jinja2Templates(directory=str(ROOT/'templates'))

@app.middleware('http')
async def security_headers(request:Request, call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    if request.url.path.startswith('/static/'):
        response.headers['Cache-Control']='public, max-age=3600'
    else:
        response.headers['Cache-Control']='no-store'
    if IS_RAILWAY:
        response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    return response

@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    finally:
        con.close()

with db() as c:
    c.executescript('''
    CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, team TEXT, role TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS meetings (id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, title TEXT NOT NULL, held_at TEXT NOT NULL, transcript TEXT NOT NULL, summary TEXT DEFAULT '', strengths TEXT DEFAULT '', improvements TEXT DEFAULT '', gaps TEXT DEFAULT '', training TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS meetings_person ON meetings(person_id);
    CREATE TABLE IF NOT EXISTS transcription_jobs (id TEXT PRIMARY KEY, person_id INTEGER NOT NULL, title TEXT NOT NULL, held_at TEXT NOT NULL, filename TEXT NOT NULL, status TEXT NOT NULL, detail TEXT DEFAULT '', meeting_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    ''')
    job_columns={row['name'] for row in c.execute('PRAGMA table_info(transcription_jobs)')}
    if 'progress' not in job_columns:
        c.execute('ALTER TABLE transcription_jobs ADD COLUMN progress INTEGER DEFAULT 0')
    if 'updated_at' not in job_columns:
        c.execute('ALTER TABLE transcription_jobs ADD COLUMN updated_at TEXT')
    if 'profile' not in job_columns:
        c.execute("ALTER TABLE transcription_jobs ADD COLUMN profile TEXT DEFAULT 'equilibrado'")
    c.execute("UPDATE transcription_jobs SET progress=100 WHERE status='concluido' AND COALESCE(progress,0)<100")
    c.execute("UPDATE transcription_jobs SET profile='equilibrado' WHERE profile IS NULL OR profile='' ")
    c.execute('CREATE INDEX IF NOT EXISTS meetings_held_at ON meetings(held_at DESC, id DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS transcription_jobs_person_created ON transcription_jobs(person_id, created_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS transcription_jobs_status ON transcription_jobs(status)')

def authed(request):
    return request.session.get('auth') is True

def protect(request):
    if not authed(request):
        raise HTTPException(status_code=303, headers={'Location':'/login'})

def csrf(request, token):
    if not token or not hmac.compare_digest(str(token), str(request.session.get('csrf',''))):
        raise HTTPException(status_code=403, detail='Formulario expirado; atualize a pagina')

def login_client_key(request):
    if IS_RAILWAY:
        forwarded=request.headers.get('x-forwarded-for','').split(',',1)[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else 'unknown'

def login_is_limited(key, register_failure=False, clear=False):
    now=time.monotonic()
    with LOGIN_ATTEMPTS_LOCK:
        if clear:
            LOGIN_ATTEMPTS.pop(key,None)
            return False
        attempts=[stamp for stamp in LOGIN_ATTEMPTS.get(key,[]) if now-stamp<LOGIN_WINDOW_SECONDS]
        if register_failure:
            attempts.append(now)
        if attempts:
            LOGIN_ATTEMPTS[key]=attempts
        else:
            LOGIN_ATTEMPTS.pop(key,None)
        return len(attempts)>=LOGIN_LIMIT

def render(request, filename, **kw):
    request.session.setdefault('csrf', secrets.token_urlsafe(24))
    return tpl.TemplateResponse(request, filename, {
        'csrf':request.session['csrf'],
        'logged':authed(request),
        'max_upload_mb':MAX_UPLOAD_MB or None,
        **kw
    })

def job_source_path(job):
    suffix=Path(job['filename']).suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        return None
    candidates=(UPLOADS/(job['id']+'.source'+suffix),UPLOADS/(job['id']+suffix))
    return next((path for path in candidates if path.is_file()),None)

def serialize_jobs(rows):
    jobs=[]
    for row in rows:
        job=dict(row)
        job['retryable']=job['status']=='erro' and job_source_path(job) is not None
        profile=TRANSCRIPTION_PROFILES.get(job.get('profile'),TRANSCRIPTION_PROFILES['equilibrado'])
        job['profile_label']=profile['label']
        jobs.append(job)
    return jobs

@app.get('/health')
def health(): return {'ok':True}

@app.get('/login', response_class=HTMLResponse)
def login_page(request:Request): return render(request, 'login.html', error=None)

@app.post('/login')
def login(request:Request, username:str=Form(...), password:str=Form(...), token:str=Form(...)):
    csrf(request, token)
    client_key=login_client_key(request)
    if login_is_limited(client_key):
        response=render(request,'login.html',error='Muitas tentativas. Aguarde 15 minutos e tente novamente.')
        response.status_code=429
        return response
    user_ok=hmac.compare_digest(username, ADMIN_USER)
    password_ok=hmac.compare_digest(password, ADMIN_PASS)
    if not (user_ok and password_ok):
        login_is_limited(client_key,register_failure=True)
        return render(request,'login.html',error='Usuario ou senha incorretos')
    login_is_limited(client_key,clear=True)
    request.session.clear()
    request.session['auth'] = True
    request.session['csrf'] = secrets.token_urlsafe(24)
    return RedirectResponse('/',303)

@app.post('/logout')
def logout(request:Request, token:str=Form(...)):
    protect(request); csrf(request,token); request.session.clear(); return RedirectResponse('/login',303)

@app.get('/', response_class=HTMLResponse)
def dashboard(request:Request):
    protect(request)
    with db() as c:
        people=[dict(x) for x in c.execute('SELECT p.*, COUNT(m.id) as total FROM people p LEFT JOIN meetings m ON m.person_id=p.id GROUP BY p.id ORDER BY p.name')]
        meetings=[dict(x) for x in c.execute('SELECT m.id,m.person_id,m.title,m.held_at,m.summary,p.name AS person FROM meetings m JOIN people p ON p.id=m.person_id ORDER BY held_at DESC,m.id DESC LIMIT 12')]
        total=c.execute('SELECT COUNT(*) FROM meetings').fetchone()[0]
    return render(request,'index.html',people=people,meetings=meetings,total=total)

@app.post('/people')
def create_person(request:Request,name:str=Form(...),team:str=Form(''),role:str=Form(''),token:str=Form(...)):
    protect(request);csrf(request,token)
    name=name.strip()
    if not name or len(name)>120:raise HTTPException(400,'Nome invalido')
    with db() as c: c.execute('INSERT INTO people(name,team,role) VALUES (?,?,?)',(name,team[:120],role[:120]))
    return RedirectResponse('/',303)

@app.get('/people/{person_id}',response_class=HTMLResponse)
def person_page(request:Request,person_id:int):
    protect(request)
    with db() as c:
        person=c.execute('SELECT * FROM people WHERE id=?',(person_id,)).fetchone()
        if not person:raise HTTPException(404)
        meetings=[dict(x) for x in c.execute('SELECT id,person_id,title,held_at,summary FROM meetings WHERE person_id=? ORDER BY held_at DESC,id DESC',(person_id,))]
        jobs=serialize_jobs(c.execute('SELECT * FROM transcription_jobs WHERE person_id=? ORDER BY created_at DESC LIMIT 15',(person_id,)))
    profiles=[{'id':key,**value} for key,value in TRANSCRIPTION_PROFILES.items()]
    return render(request,'person.html',person=dict(person),meetings=meetings,jobs=jobs,transcribe_enabled=TRANSCRIBE_ENABLED,profiles=profiles,default_profile=DEFAULT_TRANSCRIPTION_PROFILE)

@app.get('/people/{person_id}/jobs')
def transcription_jobs(request:Request,person_id:int):
    protect(request)
    with db() as c:
        if not c.execute('SELECT 1 FROM people WHERE id=?',(person_id,)).fetchone():
            raise HTTPException(404)
        jobs=serialize_jobs(c.execute('SELECT * FROM transcription_jobs WHERE person_id=? ORDER BY created_at DESC LIMIT 15',(person_id,)))
    return {'jobs':jobs}

@app.post('/people/{person_id}/meetings')
async def add_meeting(request:Request,person_id:int,title:str=Form(...),held_at:str=Form(...),transcript_text:str=Form(''),transcript_file:UploadFile|None=File(None), token:str=Form(...)):
    protect(request);csrf(request,token)
    with db() as c:
        if not c.execute('SELECT 1 FROM people WHERE id=?',(person_id,)).fetchone(): raise HTTPException(404)
    if transcript_file and transcript_file.filename:
        if not transcript_file.filename.lower().endswith(('.txt','.md')):raise HTTPException(400,'Envie apenas TXT ou MD')
        raw=await transcript_file.read(2_000_001)
        if len(raw)>2_000_000:raise HTTPException(400,'Arquivo maior que 2 MB')
        try: transcript_text=raw.decode('utf-8-sig')
        except UnicodeDecodeError: transcript_text=raw.decode('cp1252',errors='replace')
    if len(transcript_text)>2_000_000 or not transcript_text.strip():raise HTTPException(400,'Transcricao vazia ou muito grande')
    if not title.strip() or len(title)>200:raise HTTPException(400,'Titulo invalido')
    with db() as c:
        cur=c.execute('INSERT INTO meetings(person_id,title,held_at,transcript) VALUES(?,?,?,?)',(person_id,title.strip(),held_at,transcript_text))
        mid=cur.lastrowid
    return RedirectResponse(f'/meetings/{mid}',303)

@app.get('/meetings/{mid}',response_class=HTMLResponse)
def meeting_page(request:Request,mid:int):
    protect(request)
    with db() as c:
        meeting=c.execute('SELECT m.*,p.name AS person FROM meetings m JOIN people p ON p.id=m.person_id WHERE m.id=?',(mid,)).fetchone()
        if not meeting: raise HTTPException(404)
    return render(request,'meeting.html',m=dict(meeting))

@app.post('/meetings/{mid}/analysis')
def save_analysis(request:Request,mid:int,summary:str=Form(''),strengths:str=Form(''),improvements:str=Form(''),gaps:str=Form(''),training:str=Form(''),notes:str=Form(''),token:str=Form(...)):
    protect(request);csrf(request,token)
    with db() as c:
        if not c.execute('SELECT 1 FROM meetings WHERE id=?',(mid,)).fetchone():raise HTTPException(404)
        c.execute('UPDATE meetings SET summary=?,strengths=?,improvements=?,gaps=?,training=?,notes=? WHERE id=?',(*(s[:15000] for s in (summary,strengths,improvements,gaps,training,notes)),mid))
    return RedirectResponse(f'/meetings/{mid}',303)

@app.get('/meetings/{mid}/export')
def export(request:Request,mid:int):
    protect(request)
    with db() as c:
        m=c.execute('SELECT m.*, p.name person FROM meetings m JOIN people p ON p.id=m.person_id WHERE m.id=?',(mid,)).fetchone()
    if not m:raise HTTPException(404)
    content=f'''REUNIAO: {m['title']}\nCOLABORADOR: {m['person']}\nDATA: {m['held_at']}\n\nRESUMO\n{m['summary']}\n\nPONTOS FORTES\n{m['strengths']}\n\nPONTOS DE MELHORIA\n{m['improvements']}\n\nLACUNAS\n{m['gaps']}\n\nTREINAMENTOS\n{m['training']}\n\nOBSERVACOES\n{m['notes']}\n\nTRANSCRICAO\n{m['transcript']}\n'''
    return Response(content,media_type='text/plain; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="reuniao-{mid}.txt"'})

@app.post('/meetings/{mid}/delete')
def delete_meeting(request:Request,mid:int,token:str=Form(...)):
    protect(request);csrf(request,token)
    with db() as c:
        m=c.execute('SELECT person_id FROM meetings WHERE id=?',(mid,)).fetchone()
        if not m:raise HTTPException(404)
        c.execute('DELETE FROM meetings WHERE id=?',(mid,))
    return RedirectResponse(f'/people/{m[0]}',303)


def get_transcription_model(model_name):
    global TRANSCRIPTION_MODEL, TRANSCRIPTION_MODEL_NAME
    with TRANSCRIPTION_MODEL_LOCK:
        if TRANSCRIPTION_MODEL is not None and TRANSCRIPTION_MODEL_NAME == model_name:
            return TRANSCRIPTION_MODEL
        from faster_whisper import WhisperModel
        TRANSCRIPTION_MODEL = WhisperModel(
            model_name,
            device=os.getenv('WHISPER_DEVICE','cpu'),
            compute_type=os.getenv('WHISPER_COMPUTE_TYPE','int8')
        )
        TRANSCRIPTION_MODEL_NAME = model_name
        return TRANSCRIPTION_MODEL


def transcribe_job(job_id):
    def update(status, detail='', progress=0, mid=None):
        with db() as c:
            c.execute(
                'UPDATE transcription_jobs SET status=?,detail=?,progress=?,meeting_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (status, detail[:900], max(0,min(100,int(progress))), mid, job_id)
            )

    def duration_seconds(path):
        try:
            import av
            with av.open(str(path)) as container:
                if container.duration:
                    return max(float(container.duration)/1_000_000,1.0)
                stream=next((item for item in container.streams if item.type=='audio'),None)
                if stream and stream.duration is not None and stream.time_base is not None:
                    return max(float(stream.duration*stream.time_base),1.0)
        except Exception:
            pass
        return 1.0

    with db() as c:
        job=c.execute('SELECT * FROM transcription_jobs WHERE id=?',(job_id,)).fetchone()
    if not job:
        return
    source=job_source_path(job)
    if source is None:
        update('erro','O arquivo original não está mais disponível. Selecione-o novamente.',0)
        return
    person_id=job['person_id'];title=job['title'];held_at=job['held_at']
    profile_id=job['profile'] if job['profile'] in TRANSCRIPTION_PROFILES else 'equilibrado'
    profile=TRANSCRIPTION_PROFILES[profile_id]
    completed=False
    try:
        media_duration=duration_seconds(source)
        update('processando',f'Carregando perfil {profile["label"]}',8)
        model=get_transcription_model(profile['model'])
        update('processando','Transcrevendo as falas diretamente do arquivo',12)
        segments,info=model.transcribe(
            str(source),
            language='pt',
            vad_filter=True,
            beam_size=profile['beam_size']
        )
        def fmt(t):
            t=int(t)
            return f'{t//3600:02}:{(t%3600)//60:02}:{t%60:02}'
        lines=[]
        transcript_duration=max(float(getattr(info,'duration',media_duration) or media_duration),1.0)
        last_progress=11
        for seg in segments:
            if seg.text.strip():
                lines.append(f'[{fmt(seg.start)} - {fmt(seg.end)}] {seg.text.strip()}')
            progress=12+int(min(seg.end/transcript_duration,1)*83)
            if progress>last_progress:
                update('processando',f'Transcrevendo • {fmt(seg.end)} de {fmt(transcript_duration)} processados',progress)
                last_progress=progress
        if not lines:
            raise RuntimeError('Nenhuma fala identificada no arquivo.')
        update('processando','Finalizando e salvando a transcrição',97)
        transcript='\n\n'.join(lines)
        with db() as c:
            mid=c.execute('INSERT INTO meetings(person_id,title,held_at,transcript) VALUES(?,?,?,?)',(person_id,title,held_at,transcript)).lastrowid
            c.execute(
                "UPDATE transcription_jobs SET status='concluido',detail='Transcrição concluída',progress=100,meeting_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (mid,job_id)
            )
        completed=True
    except Exception as e:
        update('erro', f'{type(e).__name__}: {e}',0)
    finally:
        if completed:
            source.unlink(missing_ok=True)

def transcription_worker():
    while True:
        job_id=TRANSCRIPTION_QUEUE.get()
        try:
            transcribe_job(job_id)
        except Exception as e:
            try:
                with db() as c:
                    c.execute(
                        "UPDATE transcription_jobs SET status='erro',detail=?,progress=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (f'{type(e).__name__}: {e}'[:900],job_id)
                    )
            except Exception:
                pass
        finally:
            TRANSCRIPTION_QUEUE.task_done()

def enqueue_transcription(job_id):
    TRANSCRIPTION_QUEUE.put(job_id)

def start_transcription_worker():
    global TRANSCRIPTION_WORKER_STARTED
    if not TRANSCRIBE_ENABLED:
        return
    with TRANSCRIPTION_WORKER_LOCK:
        if TRANSCRIPTION_WORKER_STARTED:
            return
        TRANSCRIPTION_WORKER_STARTED=True
        threading.Thread(target=transcription_worker,name='transcription-worker',daemon=True).start()
    with db() as c:
        pending=list(c.execute("SELECT * FROM transcription_jobs WHERE status IN ('na_fila','processando') ORDER BY created_at,id"))
        for job in pending:
            if job_source_path(job) is None:
                c.execute(
                    "UPDATE transcription_jobs SET status='erro',detail='Arquivo original indisponível após reinício',progress=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (job['id'],)
                )
            else:
                c.execute(
                    "UPDATE transcription_jobs SET status='na_fila',detail='Retomado após reinício da aplicação',progress=2,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (job['id'],)
                )
                enqueue_transcription(job['id'])

@app.post('/transcription-jobs/{job_id}/retry')
def retry_transcription(request:Request,job_id:str,token:str=Form(...)):
    protect(request);csrf(request,token)
    if not TRANSCRIBE_ENABLED:
        raise HTTPException(400,'Transcrição não habilitada neste servidor.')
    with db() as c:
        job=c.execute('SELECT * FROM transcription_jobs WHERE id=?',(job_id,)).fetchone()
        if not job:
            raise HTTPException(404,'Processamento não encontrado.')
        if job['status']!='erro':
            raise HTTPException(409,'Este processamento não está com erro.')
        source=job_source_path(job)
        if source is None:
            raise HTTPException(409,'O arquivo original não está mais disponível. Selecione-o novamente acima.')
        changed=c.execute(
            "UPDATE transcription_jobs SET status='na_fila',detail='Nova tentativa aguardando processamento',progress=2,meeting_id=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='erro'",
            (job_id,)
        ).rowcount
        if changed!=1:
            raise HTTPException(409,'Este processamento já foi reiniciado.')
        person_id=job['person_id']
    enqueue_transcription(job_id)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JSONResponse({'ok':True,'job_id':job_id},status_code=202)
    return RedirectResponse(f'/people/{person_id}',303)

@app.post('/people/{person_id}/upload-media')
async def upload_media(request:Request,person_id:int,title:str=Form(...),held_at:str=Form(...),profile:str=Form(DEFAULT_TRANSCRIPTION_PROFILE),media:UploadFile=File(...),token:str=Form(...)):
    protect(request);csrf(request,token)
    if not TRANSCRIBE_ENABLED:
        raise HTTPException(400,'Transcrição de mídia não habilitada neste servidor. Execute a versão local ou habilite TRANSCRIBE_ENABLED=1 com faster-whisper instalado.')
    if not media.filename or Path(media.filename).suffix.lower() not in MEDIA_SUFFIXES:
        raise HTTPException(400,'Formato invalido. Use MP4, MP3, M4A, WAV ou MOV.')
    if not title.strip() or len(title)>200:raise HTTPException(400,'Titulo invalido')
    if profile not in TRANSCRIPTION_PROFILES:raise HTTPException(400,'Perfil de transcrição inválido')
    with db() as c:
        if not c.execute('SELECT 1 FROM people WHERE id=?',(person_id,)).fetchone():raise HTTPException(404)
    job_id=uuid.uuid4().hex
    dest=UPLOADS / (job_id + '.source' + Path(media.filename).suffix.lower())
    total=0
    try:
        with dest.open('wb') as f:
            while True:
                chunk=await media.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if MAX_UPLOAD and total>MAX_UPLOAD:
                    raise HTTPException(413,f'Arquivo acima de {MAX_UPLOAD_MB} MB')
                f.write(chunk)
        if not total:raise HTTPException(400,'Arquivo vazio')
        with db() as c:
            c.execute('INSERT INTO transcription_jobs(id,person_id,title,held_at,filename,status,detail,progress,profile,updated_at) VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)', (job_id,person_id,title.strip(),held_at,Path(media.filename).name[:200],'na_fila','Aguardando processamento',2,profile))
        enqueue_transcription(job_id)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JSONResponse({'ok':True,'job_id':job_id},status_code=202)
    return RedirectResponse(f'/people/{person_id}',303)

@app.get('/meetings/{mid}/gpt')
def export_for_gpt(request:Request,mid:int):
    protect(request)
    with db() as c:
        m=c.execute('SELECT m.title,m.held_at,m.transcript,p.name AS person FROM meetings m JOIN people p ON p.id=m.person_id WHERE m.id=?',(mid,)).fetchone()
    if not m:raise HTTPException(404)
    title=str(m['title']).replace('\r',' ').replace('\n',' ').strip()
    person=str(m['person']).replace('\r',' ').replace('\n',' ').strip()
    content=(
        f'# Transcrição de reunião\n\n'
        f'- **Liderado:** {person}\n'
        f'- **Reunião:** {title}\n'
        f'- **Data:** {m["held_at"]}\n'
        f'- **Idioma:** Português do Brasil\n\n'
        f'## Transcrição\n\n{m["transcript"]}\n'
    )
    return Response(content,media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="reuniao-{mid}-para-gpt.md"'})

@app.get('/meetings/{mid}/transcript')
def export_transcript(request:Request,mid:int):
    protect(request)
    with db() as c:
        m=c.execute('SELECT transcript FROM meetings WHERE id=?',(mid,)).fetchone()
    if not m:raise HTTPException(404)
    return Response(m['transcript'],media_type='text/plain; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="transcricao-{mid}.txt"'})
