# -*- coding: utf-8 -*-
"""Concat (jub0t/Concat) 无头引擎集成：通过官方 MCP 接口驱动剪辑与导出。

工作流（全部经官方 `concat --mcp headless` 的 JSON-RPC）：
  project_create → media_import(素材库文件) → clips_add_sequence(按序排布)
  → export_start/export_wait(导出成片)
掐头去尾不由 Concat 做（0.2.8 导出器不认 clip_trim，实测 3.02s 项目导出
3.53s 全长），由调用方先用 ffmpeg 精准预切，再把切好的片段喂进来。

沙箱：无头模式只写 CONCAT_MCP_ROOTS 列出的目录，项目/导出/素材都必须在其中。
"""
import json
import os
import shutil
import subprocess
import threading
import time

ENGINE_CANDIDATES = [
    '/Applications/Concat.app/Contents/MacOS/concat',   # macOS
    '/usr/bin/concat',                                   # Linux .deb/.rpm
    os.path.expanduser('~/Applications/Concat.app/Contents/MacOS/concat'),
]

class ConcatEngine:
    """一次任务一个进程：spawn `concat --mcp headless` 并走 MCP stdio 协议。"""

    def __init__(self, binary, roots):
        self.binary = binary
        self.roots = roots
        env = dict(os.environ)
        for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
            env.pop(k, None)
        env['CONCAT_MCP_ROOTS'] = roots
        self.p = subprocess.Popen([binary, '--mcp', 'headless'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env,
                                  text=True, bufsize=1)
        self._id = 0
        self._send({'jsonrpc': '2.0', 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'luna-sync', 'version': '1.0'}}})
        self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj) + '\n')
        self.p.stdin.flush()

    def call(self, method, params, timeout=120):
        self._id += 1
        rid = self._id
        self._send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError('concat engine stdout closed')
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get('id') == rid:
                if 'error' in msg:
                    raise RuntimeError('concat mcp: ' + json.dumps(msg['error'])[:200])
                res = msg.get('result') or {}
                texts = [c.get('text', '') for c in res.get('content', [])
                         if c.get('type') == 'text']
                text = '\n'.join(texts)
                try:
                    return {'text': text, 'json': json.loads(text)}
                except ValueError:
                    return {'text': text, 'json': None}
        raise RuntimeError('concat mcp timeout: ' + method)

    def tool(self, name, arguments, timeout=300):
        return self.call('tools/call', {'name': name, 'arguments': arguments}, timeout)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.terminate()
            self.p.wait(timeout=10)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass

def detect_engine(explicit=None):
    for cand in ([explicit] if explicit else []) + ENGINE_CANDIDATES:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    which = shutil.which('concat')
    return which

def _adapt_video(eng, video):
    """按引擎版本适配画幅参数：0.2.8={w,h,fps}；≥0.2.9={w,h,rateNum,rateDen}。"""
    if not video:
        return None
    try:
        ver = (eng.tool('concat_version', {}, 30)['json'] or {}).get('concat', '')
    except Exception:
        ver = ''
    # '0.2.9' → (0,2,9)：注意 9 在 patch 位（.split('.')[1] 是 minor=2，别用错）
    try:
        ver_t = tuple(int(x) for x in ver.split('.') if x.isdigit())
    except ValueError:
        ver_t = ()
    shape = dict(video)
    fps = shape.pop('fps', None)
    if ver_t and ver_t >= (0, 2, 9):
        shape.setdefault('rateNum', max(1, round((fps or 30) * 1000)))
        shape.setdefault('rateDen', 1000)
    elif fps is not None:
        shape['fps'] = fps
    return shape

def run_concat_job(binary, work_root, media_files, out_path, progress=None, video=None):
    """把（已按有效区间预切好的）片段序列交给 Concat 引擎拼接导出。

    media_files: 有序绝对路径列表；out_path: 成片输出绝对路径。
    返回导出文件路径。progress(done,total) 可选回调。
    video 可选画幅（width/height/fps）；版本 schema 不同由 _adapt_video
    自动适配（0.2.8 认 fps，0.2.9 认 rateNum/rateDen）。
    """
    projects = os.path.join(work_root, 'projects')
    exports = os.path.join(work_root, 'exports')
    for d in (projects, exports):
        os.makedirs(d, exist_ok=True)
    roots = ':'.join({os.path.dirname(os.path.commonprefix([os.path.dirname(f) + os.sep
                                                            for f in media_files]) or work_root)
                      for f in media_files} | {work_root})
    # 素材可能分处不同子目录——统一把公共库根与工作根都放进沙箱
    roots = ':'.join(sorted({work_root} | {os.path.dirname(f) for f in media_files} |
                            {os.path.dirname(os.path.dirname(media_files[0]))}))
    eng = ConcatEngine(binary, roots)
    try:
        name = 'luna-%d' % int(time.time() * 1000 % 100000000)
        create_args = {'location': projects, 'name': name}
        vshape = _adapt_video(eng, video)
        if vshape:
            create_args['video'] = vshape
        r = eng.tool('project_create', create_args, 60)
        proj = os.path.join(projects, name)
        # 0.2.9 create 即 open；老版/异常路径兜底显式 open（已开则幂等返回）
        try:
            eng.tool('project_open', {'path': proj}, 60)
        except Exception:
            pass
        ids = []
        for f in media_files:
            r = eng.tool('media_import', {'path': proj, 'file': f}, 300)
            cid = (r['json'] or {}).get('createdId')
            if not cid:
                raise RuntimeError('concat media_import failed: ' + os.path.basename(f))
            ids.append(cid)
            if progress:
                progress(min(ids.index(cid) + 1, len(ids)), len(ids))
        eng.tool('clips_add_sequence', {'path': proj, 'mediaIds': ids, 'start': 0}, 300)
        if progress:
            progress(len(ids), len(ids))
        engine_out = os.path.join(exports, 'luna-concat.mp4')
        r = eng.tool('export_start', {'path': proj, 'output': engine_out,
                                      'codec': 'h264'}, 60)
        job = (r['json'] or {}).get('job')
        if not job:
            raise RuntimeError('concat export_start failed: ' + (r['text'] or '')[:120])
        st = {}
        for _ in range(120):
            st = eng.tool('export_wait', {'job': job, 'timeoutSeconds': 30}, 90)['json'] or {}
            if st.get('state') in ('done', 'failed', 'cancelled'):
                break
            time.sleep(1)
        if st.get('state') != 'done' or not os.path.isfile(engine_out) or os.path.getsize(engine_out) == 0:
            raise RuntimeError('concat export failed: ' + json.dumps(st)[:200])
        if os.path.exists(out_path):
            os.remove(out_path)
        shutil.move(engine_out, out_path)
        try:
            eng.tool('project_close', {'path': proj, 'save': False}, 60)
        except Exception:
            pass
        return out_path
    finally:
        eng.close()
