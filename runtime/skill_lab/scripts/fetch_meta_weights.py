"""Download approved official Meta weights; optional credential arrives over stdin.

The secret is never saved or logged. Supply it over SSH stdin from an existing
Hugging Face login, or omit --token-stdin to use the server's login/HF_TOKEN.
"""
import argparse,hashlib,json,os,time,urllib.request
from pathlib import Path

MODELS={
 'sam3':('facebook/sam3',['sam3.pt','LICENSE']),
 'objects':('facebook/sam-3d-objects',['checkpoints/pipeline.yaml','checkpoints/ss_generator.ckpt','checkpoints/ss_generator.yaml','checkpoints/slat_generator.ckpt','checkpoints/slat_generator.yaml','checkpoints/ss_decoder.ckpt','checkpoints/ss_decoder.yaml','checkpoints/slat_decoder_gs.ckpt','checkpoints/slat_decoder_gs.yaml','checkpoints/slat_decoder_gs_4.ckpt','checkpoints/slat_decoder_gs_4.yaml','checkpoints/slat_decoder_mesh.ckpt','checkpoints/slat_decoder_mesh.yaml','LICENSE']),
}
def main():
 p=argparse.ArgumentParser();p.add_argument('model',choices=MODELS);p.add_argument('destination',type=Path);p.add_argument('--token-stdin',action='store_true');a=p.parse_args()
 if a.token_stdin:
  import sys
  token=sys.stdin.read().strip()
 else:
  token=os.environ.get('HF_TOKEN','')
  if not token:
   token=(Path.home()/'.cache/huggingface/token').read_text().strip()
 repo,names=MODELS[a.model];headers={'Authorization':'Bearer '+token}
 request=urllib.request.Request(f'https://huggingface.co/api/models/{repo}/tree/main?recursive=true',headers=headers)
 listing=json.load(urllib.request.urlopen(request,timeout=30))
 for name in names:
  info=next(i for i in listing if i.get('path')==name);out=a.destination/name;out.parent.mkdir(parents=True,exist_ok=True)
  expected=info.get('lfs',{}).get('oid')
  if out.exists() and out.stat().st_size==info['size']:
   with out.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
   if not expected or digest==expected:print('Verified existing:',name,flush=True);continue
  print('Downloading',name,round(info['size']/1024**3,2),'GiB',flush=True)
  request=urllib.request.Request(f'https://huggingface.co/{repo}/resolve/main/{name}',headers=headers)
  digest=hashlib.sha256();count=0;start=time.monotonic()
  with urllib.request.urlopen(request,timeout=180) as response,out.with_suffix('.download').open('wb') as f:
   while chunk:=response.read(8*1024*1024):
    f.write(chunk);digest.update(chunk);count+=len(chunk)
    if count%(1024**3)==0:print(name,count//1024**2,'MiB',int(time.monotonic()-start),'seconds',flush=True)
  if count!=info['size'] or (expected and digest.hexdigest()!=expected):raise ValueError('Checkpoint verification failed')
  out.with_suffix('.download').replace(out);print('Verified:',name,flush=True)
if __name__=='__main__':main()
