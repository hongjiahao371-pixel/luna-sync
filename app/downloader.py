import os, re, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def _content_range(value):
    match = re.fullmatch(r'bytes\s+(\d+)-(\d+)/(\d+|\*)', str(value or '').strip())
    if not match:
        return None, None
    total = None if match.group(3) == '*' else int(match.group(3))
    return int(match.group(1)), total


def download_file(url, dest, on_progress=None, cancel=None, chunk=262144, expected_size=None):
    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
    partial = dest + '.part'
    name = os.path.basename(dest)
    expected_size = int(expected_size) if expected_size is not None else None

    if os.path.exists(dest) and not os.path.exists(partial):
        sz = os.path.getsize(dest)
        if expected_size is None or sz == expected_size:
            if on_progress:
                on_progress(name, sz, sz, 0)
            return dest
        if sz < expected_size:
            os.replace(dest, partial)
        else:
            os.remove(dest)

    total = expected_size
    if total is None:
        try:
            r = urlopen(Request(url, headers={'User-Agent': 'LunaDL/0.1', 'Range': 'bytes=0-0'}), timeout=10)
            _, total = _content_range(r.headers.get('Content-Range'))
            if total is None and getattr(r, 'status', 200) == 200:
                length = r.headers.get('Content-Length')
                total = int(length) if length else None
            r.close()
        except Exception:
            pass

    existing = os.path.getsize(partial) if os.path.exists(partial) else 0
    if total and existing == total:
        os.replace(partial, dest)
        if on_progress:
            on_progress(name, total, total, 0)
        return dest
    if total and existing > total:
        os.remove(partial)
        existing = 0

    def open_response(offset):
        headers = {'User-Agent': 'LunaDL/0.1'}
        if offset > 0:
            headers['Range'] = 'bytes=' + str(offset) + '-'
        return urlopen(Request(url, headers=headers), timeout=30)

    try:
        resp = open_response(existing)
    except HTTPError as e:
        if existing <= 0 or e.code != 416:
            raise
        if total and existing == total:
            os.replace(partial, dest)
            return dest
        os.remove(partial)
        existing = 0
        resp = open_response(0)

    status = getattr(resp, 'status', 200)
    response_start, response_total = _content_range(resp.headers.get('Content-Range'))
    if response_total is not None:
        total = response_total
    if existing > 0 and (status != 206 or response_start != existing):
        resp.close()
        os.remove(partial)
        existing = 0
        resp = open_response(0)
        status = getattr(resp, 'status', 200)
        _, response_total = _content_range(resp.headers.get('Content-Range'))
        if response_total is not None:
            total = response_total
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
    if total and dl != total:
        raise OSError('incomplete ' + str(dl) + '/' + str(total))
    os.replace(partial, dest)
    if on_progress:
        on_progress(name, dl, total or dl, 0)
    return dest
