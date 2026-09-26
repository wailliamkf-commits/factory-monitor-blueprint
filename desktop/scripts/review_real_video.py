"""Local-only review of actual video and previously computed observations.

No inference is executed by this viewer. It serves only an explicit manifest's
videos and data on loopback, under a random URL, without filesystem browsing.
"""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
from inspect_real_video import file_sha256, validate_profile

HTML = '''<!doctype html><meta charset="utf-8"><title>真实录像检查</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101719;color:#eaf2ef;font:15px system-ui,sans-serif}main{max-width:1320px;margin:auto;padding:24px}h1{font-size:26px;margin:4px 0 8px}p{color:#b8c9c4;line-height:1.6}button{background:#243933;border:1px solid #4b6f62;border-radius:8px;color:inherit;padding:10px 18px;margin-right:8px;cursor:pointer}button.active{background:#365f50}header{display:flex;justify-content:space-between;align-items:center}.badge{border:1px solid #7c9f58;color:#d3e9b7;padding:8px 14px;border-radius:20px}.body{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:20px;margin-top:20px}.stage{position:relative;background:#050807;border:1px solid #3e514a}video{width:100%;display:block}canvas{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none}.card{padding:16px;background:#1a2723;border-radius:10px;margin-bottom:12px}.small{font-size:12px;color:#adbbb5}#states{line-height:1.7}.state{border-bottom:1px solid #385047;padding:6px 0}.unknown{color:#edcb82}.okay{color:#9be1bd}#status{min-height:48px}.fine{font-size:13px;color:#b9c9c1;line-height:1.7}a{color:#a5d9c6}@media(max-width:950px){.body{display:block}}
</style><main><header><div><div class="small">FACTORY MONITOR · 本机检查</div><h1>用真实录像验证画面读取</h1></div><span class="badge">离线检测结果回放</span></header>
<p>原始 Seetong 录像 + 本地计算结果。此页没有运行实时模型，也不代表当前现场。</p>
<div id="choices"></div><div class="body"><section><div class="stage"><video id="video" controls muted preload="metadata"></video><canvas id="overlay"></canvas></div><p id="status"></p><div class="card fine">识别按报告中的间隔抽样。检测框只在对应样本附近显示，不能用于查清所有动作。没有框不代表无人；“时间未知”不代表摄像头卡住。播放器暂停不会触发现场告警。</div></section><aside><div class="card"><b>本次处理</b><p id="summary"></p><div class="small" id="position"></div></div><div class="card"><b>逐路读钟</b><div id="states"></div></div></aside></div></main>
<script>
const v=document.getElementById('video'), c=document.getElementById('overlay'), g=c.getContext('2d');
let data,current,rows=[],lastDraw=0;
const labels={advancing:'时间在推进',unknown:'时间未知',suspected_stalled:'疑似停顿',layout_unknown:'布局未校准'};
function pick(i){current=data.clips[i];rows=current.rows;v.pause();v.src='media/'+i;document.querySelectorAll('button').forEach((b,j)=>b.classList.toggle('active',i===j));let r=current.report;document.getElementById('summary').textContent='读钟每 '+r.sample_step_seconds+' 秒、人物检测每 '+(r.detection_sample_seconds??30)+' 秒抽样。可解析时间 '+r.parseable_clock_samples+' / '+r.clock_samples+' 次；'+r.detector_batch_count+' 次人物检测批次。可解析率不是识别准确率。';draw();}
function draw(){if(!current)return;let t=v.currentTime||0;c.width=current.profile.size[0];c.height=current.profile.size[1];g.clearRect(0,0,c.width,c.height);document.getElementById('position').textContent='录像位置 '+t.toFixed(1)+' 秒';const seg=current.profile.segments.find(s=>s.start<=t&&t<s.end);let nearest=-1;for(let row of rows){if(row.pts<=t&&row.pts>nearest)nearest=row.pts;}const active=rows.filter(r=>r.pts===nearest&&t-nearest<=current.report.sample_step_seconds+.2);let items=[];if(seg){for(let cam of seg.cameras){let r=active.find(r=>r.camera===cam.id&&r.layout===seg.id);let state=r?r.state:'unknown';items.push(cam.id+'：'+(labels[state]||state)+(r&&r.clock?' · '+r.clock.slice(11):''));if(r&&r.person_boxes&&t-r.pts<1){g.strokeStyle='#a6f2bc';g.lineWidth=2;for(let b of r.person_boxes)g.strokeRect(cam.crop[0]+b[0],cam.crop[1]+b[1],b[2]-b[0],b[3]-b[1]);}}}else{items=['布局切换 / 未校准区间：暂停解释画面'];}let el=document.getElementById('states');el.replaceChildren(...items.map(text=>{let p=document.createElement('div');p.className='state';p.textContent=text;return p;}));document.getElementById('status').textContent=v.ended?'录像已结束；没有继续监测。':seg?'正在查看真实录屏及已保存的抽样结果。':'当前区间不作行为判断，也不判定为无人。';}
fetch('data').then(r=>r.json()).then(d=>{data=d;let e=document.getElementById('choices');d.clips.forEach((clip,i)=>{let b=document.createElement('button');b.textContent=clip.label;b.onclick=()=>pick(i);e.append(b)});pick(0)});
v.addEventListener('timeupdate',draw);v.addEventListener('loadedmetadata',draw);v.addEventListener('seeked',draw);v.addEventListener('ended',draw);v.addEventListener('error',()=>{document.getElementById('status').textContent='视频读取失败；请检查本机原文件是否仍在。'});
</script>'''


def range_bounds(value, size):
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value or '')
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError('Invalid range')
    a, b = match.groups()
    if not a:
        amount = int(b)
        if amount <= 0:
            raise ValueError('Invalid suffix')
        start, end = max(0, size-amount), size-1
    else:
        start, end = int(a), min(size-1, int(b) if b else size-1)
    if start >= size or end < start:
        raise ValueError('Unsatisfiable range')
    return start, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--url-file', required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    clips, media = [], []
    for item in manifest['clips']:
        path = Path(item['video']).resolve(strict=True)
        if path.suffix.lower() != '.mp4':
            raise ValueError('This browser viewer accepts MP4 recordings only')
        report = json.loads(Path(item['report']).read_text())
        profile = json.loads(Path(item['profile']).read_text())
        validate_profile(profile)
        if (file_sha256(path) != report['source_sha256']
                or file_sha256(item['profile']) != report['profile_sha256']):
            raise ValueError('Source/profile do not match the saved analysis; refuse wrong overlays')
        rows = []
        for line in Path(item['observations']).read_text().splitlines():
            source = json.loads(line)
            # The browser does not need OCR raw response paths or other local metadata.
            rows.append({k:v for k,v in source.items() if k in
                         {'pts','camera','layout','clock','state','person_boxes'}})
        media.append(path)
        clips.append({'label':item['label'],'report':report,'profile':profile,'rows':rows})
    payload = json.dumps({'clips':clips}, ensure_ascii=False).encode()
    token = secrets.token_urlsafe(24)
    prefix = f'/{token}/'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.send_error(403)
                return
            if not self.path.startswith(prefix):
                self.send_error(404)
                return
            tail = self.path[len(prefix):]
            if tail in {'', 'data'}:
                data = HTML.encode() if not tail else payload
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8' if not tail else 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline'; media-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
                return
            match = re.fullmatch(r'media/(\d+)', tail)
            if not match or int(match[1]) >= len(media):
                self.send_error(404)
                return
            path = media[int(match[1])]
            size = path.stat().st_size
            value = self.headers.get('Range')
            try:
                start, end = range_bounds(value, size) if value else (0, size-1)
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(206 if value else 200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end-start+1))
            if value:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with path.open('rb') as stream:
                    stream.seek(start)
                    remaining = end-start+1
                    while remaining:
                        block = stream.read(min(256*1024, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    args.url_file.write_text(f'http://127.0.0.1:{server.server_port}{prefix}')
    print('Local review ready; Ctrl+C stops the server. No ongoing inference.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
