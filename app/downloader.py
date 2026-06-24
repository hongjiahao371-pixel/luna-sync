import os, re, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

def download_file(url, dest, on_progress=None, cancel=None, chunk=262144):
    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
    partial = dest + '.part'
    name = os.path.basename(dest)

    if os.path.exists(dest) and not os.path.exists(partial):
        sz = os.path.getsize(dest)
        if on_progress:
            on_progress(name, sz, sz, 0)
        return dest

    total = None
    try:
        r = urlopen(Request(url, headers={'User-Agent': 'LunaDL/0.1', 'Range': 'bytes=0-0'}), timeout=10)
        cr = r.headers.get('Content-Range')
        if cr:
            m = re.search(r'/(\d+)', cr)
            if m:
                total = int(m.group(1))
        r.close()
    except Exception:
        pass

    existing = os.path.getsize(partial) if os.path.exists(partial) else 0
    if total and existing >= total:
        os.replace(partial, dest)
        if on_progress:
            on_progress(name, total, total, 0)
        return dest

    headers = {'User-Agent': 'LunaDL/0.1'}
    if existing > 0:
        headers['Range'] = 'bytes=' + str(existing) + '-'
    try:
        resp = urlopen(Request(url, headers=headers), timeout=30)
    except HTTPError as e:
        if existing > 0 and e.code == 416:
            if total and existing >= total:
                os.replace(partial, dest)
                return dest
            os.remove(partial)
            existing = 0
            resp = urlopen(Request(url, headers={'User-Agent': 'LunaDL/0.1'}), timeout=30)
        else:
            raise

    status = getattr(resp, 'status', 200)
    if existing > 0 and status == 200:
        os.remove(partial)
        existing = 0
    if total is None:
        cl = resp.headers.get('Content-Length')
        if cl:
            total = existing + int(cl) if status == 206 else int(cl)

    mode = 'ab' if existing > 0 else 'wb'
    dl = existing
    started = time.monotonic()
    last = 0.0
    with resp:
        with open(partial, mode) as f:
            while True:
                if cancel and cancel.is_set():
                    raise Exception('cancelled')
                ch = resp.read(chunk)
                if not ch:
                    break
                f.write(ch)
                dl += len(ch)
                now = time.monotonic()
                if on_progress and (now - last > 0.1 or (total and dl >= total)):
                    on_progress(name, dl, total, (dl - existing) / max(now - started, 0.001))
                    last = now
                if total and dl >= total:
                    break
    if total and dl < total:
        raise OSError('incomplete ' + str(dl) + '/' + str(total))
    os.replace(partial, dest)
    if on_progress:
        on_progress(name, dl, total or dl, 0)
    return dest
