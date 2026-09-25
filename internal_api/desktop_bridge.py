"""Direct Docker/Node-inspector bridge to the logged-in desktop client."""

import json
import os
import posixpath
import subprocess
import threading
import time
from pathlib import Path

CONTAINER = os.environ.get("BAIDU_CONTAINER", "baidunetdisk")
ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_ROOT = Path(os.environ.get("BAIDU_DOWNLOAD_ROOT", "/home/sun/downloads"))
CONTAINER_EVAL = "/config/internal_api/inspector_eval.py"
APP_EXPR = "process.mainModule.require('electron').app"
EVAL_LOCK = threading.Lock()

class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def docker(*args, input_text=None, timeout=20):
    try:
        return subprocess.run(
            ["docker", *args], input=input_text, text=True, capture_output=True,
            timeout=timeout, check=True,
        ).stdout
    except subprocess.TimeoutExpired as exc:
        raise ApiError(504, "BaiduNetdisk internal call timed out") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "docker command failed").strip()
        raise ApiError(502, message) from exc


def ensure_inspector():
    probe = subprocess.run(
        ["docker", "exec", CONTAINER, "sh", "-lc", "curl -fsS --max-time 1 http://127.0.0.1:9229/json/list >/dev/null"],
        capture_output=True,
    )
    if probe.returncode == 0:
        return
    pid = docker(
        "exec", CONTAINER, "sh", "-lc",
        "pgrep -o -f '^/opt/baidunetdisk/baidunetdisk --no-sandbox' || pgrep -o baidunetdisk",
    ).strip()
    docker("exec", CONTAINER, "kill", "-USR1", pid)
    for _ in range(30):
        probe = subprocess.run(
            ["docker", "exec", CONTAINER, "sh", "-lc", "curl -fsS --max-time 1 http://127.0.0.1:9229/json/list >/dev/null"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.1)
    raise ApiError(502, "could not enable the Electron Node inspector")


def evaluate(expression, timeout=30):
    with EVAL_LOCK:
        ensure_inspector()
        output = docker(
            "exec", "-i", CONTAINER, "python3", CONTAINER_EVAL,
            input_text=json.dumps({"expression": expression}, ensure_ascii=False),
            timeout=timeout,
        )
    response = json.loads(output)
    if not response.get("ok"):
        raise ApiError(502, response.get("error", "internal evaluation failed"))
    return response.get("value")


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def list_files(path="/", page=1, page_size=1000, sort_type=0, desc=True, refresh=False, cid="0"):
    event = f"__baidu_internal_api_{time.time_ns()}"
    options = {
        "vuexModule": event,
        "vuexErrorModule": event + "_error",
        "operate": "replaceAll",
        "dirPath": path,
        "curPage": page,
        "pageCount": page_size,
        "sortType": sort_type,
        "isSortDesc": desc,
        "isForceRefresh": refresh,
        "browserwindow": "mainBrowserwindow",
        "cid": str(cid),
    }
    expression = f"""
new Promise((resolve, reject) => {{
  const app = {APP_EXPR};
  const wc = app.mainBrowserwindow.webContents;
  const originalSend = wc.send;
  const eventName = {js(event)};
  const errorName = eventName + '_error';
  let settled = false;
  const cleanup = () => {{ wc.send = originalSend; clearTimeout(timer); }};
  const timer = setTimeout(() => {{
    if (!settled) {{ settled = true; cleanup(); reject(new Error('file list timeout')); }}
  }}, 20000);
  wc.send = function(channel, ...args) {{
    if (channel === eventName || channel === errorName) {{
      if (!settled) {{
        settled = true;
        cleanup();
        channel === eventName ? resolve(args[0]) : reject(new Error(JSON.stringify(args[0])));
      }}
      return;
    }}
    return originalSend.apply(this, [channel, ...args]);
  }};
  try {{ app.$fetchFileList({js(options)}); }} catch (error) {{
    settled = true; cleanup(); reject(error);
  }}
}})
"""
    result = evaluate(expression, timeout=30)
    return result.get("data", result) if isinstance(result, dict) else result


def container_path(local_path):
    path = Path(local_path).expanduser().resolve()
    mappings = [
        (ROOT.resolve(), Path("/config")),
        (DOWNLOAD_ROOT.resolve(), Path("/config/baidunetdiskdownload")),
    ]
    for host_root, target_root in mappings:
        try:
            return str(target_root / path.relative_to(host_root))
        except ValueError:
            continue
    if str(local_path).startswith("/config/") or str(local_path) == "/config":
        return str(local_path)
    raise ApiError(400, f"local path must be under {ROOT}, {DOWNLOAD_ROOT}, or /config")


def assert_container_paths(paths):
    for path in paths:
        check = subprocess.run(["docker", "exec", CONTAINER, "test", "-e", path])
        if check.returncode != 0:
            raise ApiError(400, f"local path does not exist in container: {path}")


def is_container_dir(path):
    return subprocess.run(["docker", "exec", CONTAINER, "test", "-d", path]).returncode == 0


def upload(local_paths, server_path="/", cid="0"):
    paths = [container_path(path) for path in local_paths]
    if not paths:
        raise ApiError(400, "local_paths must not be empty")
    assert_container_paths(paths)
    items = [{"local_path": path, "is_dir": 1 if is_container_dir(path) else 0} for path in paths]
    expression = (
        f"(() => {{ const app={APP_EXPR}; "
        f"return app.$uploader.addFiles({js(items)},{js(server_path)},String({js(cid)})); }})()"
    )
    return {"code": evaluate(expression), "files": items, "server_path": server_path}


def normalize_remote_path(path):
    normalized = posixpath.normpath("/" + str(path).lstrip("/"))
    if normalized == "/.":
        return "/"
    return normalized


def resolve_download_items(paths, cid="0", refresh=False):
    grouped = {}
    for raw_path in paths:
        path = normalize_remote_path(raw_path)
        if path == "/":
            raise ApiError(400, "the remote root cannot be downloaded as one item")
        grouped.setdefault(posixpath.dirname(path) or "/", []).append(path)
    found = {}
    for parent, wanted in grouped.items():
        result = list_files(parent, 1, 1000, 0, True, refresh, cid)
        for item in result.get("files", []):
            if item.get("path") in wanted:
                found[item["path"]] = item
    missing = [path for path in paths if normalize_remote_path(path) not in found]
    if missing:
        raise ApiError(404, "remote path not found in client file list: " + ", ".join(missing))
    return [
        {
            "md5": found[normalize_remote_path(path)].get("md5", ""),
            "size": found[normalize_remote_path(path)].get("size", 0),
            "is_dir": found[normalize_remote_path(path)].get("is_dir", 0),
            "server_path": normalize_remote_path(path),
        }
        for path in paths
    ]


def download(paths, local_path="/config/baidunetdiskdownload", cid="0", refresh=False):
    destination = container_path(local_path)
    assert_container_paths([destination])
    items = resolve_download_items(paths, cid, refresh)
    for item in items:
        item["local_path"] = destination
    expression = (
        f"(() => {{ const app={APP_EXPR}; "
        f"return app.$downloader.addDownloadTask({js(items)},'self',{str(bool(refresh)).lower()},String({js(cid)}),0); }})()"
    )
    return {"code": evaluate(expression), "files": items, "local_path": destination}


def tasks(kind="download", cid="0"):
    if kind not in ("download", "upload"):
        raise ApiError(400, "type must be download or upload")
    prop = "$downloader" if kind == "download" else "$uploader"
    method = "getDownloadTasks" if kind == "download" else "getUploadTasks"
    args = "0, 1000, cid" if kind == "download" else "1000, cid"
    expression = f"""
new Promise((resolve, reject) => {{
  const app={APP_EXPR}; const cid={js(str(cid))};
  const timer=setTimeout(() => reject(new Error('task list timeout')), 10000);
  app.{prop}.{method}((errorNo, flag, items, count) => {{
    clearTimeout(timer); resolve({{errorNo, flag, items, count}});
  }}, {args});
}})
"""
    return evaluate(expression, timeout=20)
