"""Fixed, independently executable downstream stages. Inputs are never modified."""
import argparse, hashlib, html, json, os, sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--stage',required=True)
    args=parser.parse_args()
    cfg=json.loads(Path(args.config).read_text(encoding='utf-8'))
    out=Path(cfg['outdir']);out.mkdir(parents=True,exist_ok=False)
    stage=args.stage
    registry=json.loads(Path(__file__).with_name('stages.json').read_text())
    if stage not in registry:raise ValueError('Unknown fixed stage')
    spec=registry[stage]
    os.environ.setdefault('MPLBACKEND','Agg')
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS'):os.environ[name]=str(cfg.get('cpu',1))
    os.environ['NUMBA_CACHE_DIR']=str(out/'.numba')
    os.environ['MPLCONFIGDIR']=str(out/'.matplotlib')
    if spec['action']=='report':
        root=out.parents[1]
        rows=[]
        for file in sorted(root.glob('*/results/*')):
            if not file.is_file() or file==out/'report.html':continue
            relative=os.path.relpath(file,out).replace(os.sep,'/')
            rows.append('<li><a href="'+html.escape(relative,quote=True)+'">'+html.escape(file.parent.parent.name+' / '+file.name)+'</a> ('+str(file.stat().st_size)+' bytes)</li>')
        (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>OMaaX 分析报告</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;line-height:1.8}a{color:#625bce}pre{white-space:pre-wrap}</style><h1>'+html.escape(spec['name'])+'</h1><p>本报告汇总本次实际生成的分析文件。未运行的步骤不会生成结果；统计和生物学解释由使用者判断。</p><ul>'+''.join(rows)+'</ul>',encoding='utf-8')
    else:
        module=__import__(spec['module'])
        module.execute(spec['action'],cfg,out)
    missing=[name for name in spec['outputs'] if not (out/name).is_file() or (out/name).stat().st_size==0]
    if missing:raise ValueError('步骤未生成预期结果：'+', '.join(missing))
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    (out/'stage-record.json').write_text(json.dumps({'stage':stage,'software':spec['software'],'parameters':cfg,'files':files},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
