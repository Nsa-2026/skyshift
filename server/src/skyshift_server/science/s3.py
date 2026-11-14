"""Anonymous access to the public SPHEREx bucket on AWS (nasa-irsa-spherex).

Everything goes through plain HTTPS with Range requests, so no AWS credentials,
no TAP service and no full-file downloads are needed.
"""
import os, re, time, hashlib, threading
import requests

BUCKET_URL = os.environ.get("SPXTM_BUCKET", "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com")
CACHE_DIR = os.path.expanduser(os.environ.get("SKYSHIFT_SCIENCE_CACHE", os.environ.get("SPXTM_CACHE", "~/.cache/skyshift/science")))


_local = threading.local()


def session():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        a = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64)
        s.mount("https://", a)
        _local.s = s
    return s


def _get(url, headers=None, params=None, tries=5):
    err = None
    for i in range(tries):
        try:
            r = session().get(url, headers=headers, params=params, timeout=90)
            if r.status_code in (200, 206):
                return r
            if r.status_code in (404, 416):
                r.raise_for_status()
            err = RuntimeError(f"HTTP {r.status_code} for {url}")
        except requests.HTTPError:
            raise
        except Exception as e:  # network hiccup
            err = e
        time.sleep(0.5 * 2 ** i)
    raise err


def list_prefixes(prefix):
    """Immediate sub-'folders' of prefix."""
    out, tok = [], None
    while True:
        p = {"list-type": "2", "prefix": prefix, "delimiter": "/", "max-keys": "1000"}
        if tok:
            p["continuation-token"] = tok
        t = _get(BUCKET_URL, params=p).text
        out += [x for x in re.findall(r"<Prefix>(.*?)</Prefix>", t) if x != prefix]
        m = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", t)
        if not m:
            return out
        tok = m.group(1)


def list_keys(prefix, max_keys=None):
    out, tok = [], None
    while True:
        p = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if tok:
            p["continuation-token"] = tok
        t = _get(BUCKET_URL, params=p).text
        out += re.findall(r"<Key>(.*?)</Key>", t)
        if max_keys and len(out) >= max_keys:
            return out[:max_keys]
        m = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", t)
        if not m:
            return out
        tok = m.group(1).replace("&amp;", "&")


def get_range(key, start, stop, cache=True):
    """Bytes [start, stop) of an object. Small reads are cached on disk."""
    path = None
    if cache:
        h = hashlib.sha1(f"{key}:{start}:{stop}".encode()).hexdigest()
        path = os.path.join(CACHE_DIR, "rng", h[:2], h)
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    r = _get(f"{BUCKET_URL}/{key}", headers={"Range": f"bytes={start}-{stop - 1}"})
    data = r.content
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return data


def http_url(key):
    return f"{BUCKET_URL}/{key}"
