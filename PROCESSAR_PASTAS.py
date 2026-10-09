from pathlib import Path
import subprocess, time, os, traceback
from faster_whisper import WhisperModel
base=Path(__file__).resolve().parent
entrada=base/'Entrada'; convertidos=base/'Convertidos'; transcricoes=base/'Transcricoes'
for folder in (entrada,convertidos,transcricoes):folder.mkdir(exist_ok=True)
model=WhisperModel(os.getenv('WHISPER_MODEL','small'),device='cpu',compute_type='int8')
def log(s):
    with (base/'automacao.log').open('a',encoding='utf-8') as f:f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+s+'\n')
def stable(p):
    a=p.stat().st_size;time.sleep(5)
    return p.exists() and p.stat().st_size==a and a>0
def ts(n):
    n=int(n);return f'{n//3600:02}:{(n%3600)//60:02}:{n%60:02}'
log('Monitor iniciado')
while True:
    try:
        for vid in entrada.glob('*.mp4'):
            mp3=convertidos/(vid.stem+'.mp3')
            if mp3.exists() or not stable(vid):continue
            tmp=convertidos/(vid.stem+'.part.mp3')
            try:
                subprocess.run(['ffmpeg','-nostdin','-y','-loglevel','error','-i',str(vid),'-vn','-ac','1','-ar','44100','-b:a','96k',str(tmp)],check=True)
                tmp.replace(mp3);log('Convertido: '+vid.name)
            except Exception as ex:log('ERRO conversao: '+str(ex));tmp.unlink(missing_ok=True)
        for mp3 in convertidos.glob('*.mp3'):
            if mp3.name.endswith('.part.mp3'):continue
            txt=transcricoes/(mp3.stem+'.txt')
            if txt.exists() or not stable(mp3):continue
            temp=transcricoes/(mp3.stem+'.part.txt')
            try:
                seg,_=model.transcribe(str(mp3),language='pt',vad_filter=True,beam_size=5)
                with temp.open('w',encoding='utf-8') as f:
                    for part in seg:
                        if part.text.strip():f.write(f'[{ts(part.start)} - {ts(part.end)}] {part.text.strip()}\n\n')
                if temp.stat().st_size==0:raise RuntimeError('Sem falas identificadas')
                temp.replace(txt);log('Transcrito: '+mp3.name)
            except Exception as ex:log('ERRO transcricao: '+str(ex));temp.unlink(missing_ok=True)
    except Exception as ex:log('ERRO geral: '+str(ex)+' '+traceback.format_exc())
    time.sleep(15)
