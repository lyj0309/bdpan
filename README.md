# bdpan

Python SDK for an already logged-in BaiduNetdisk desktop client running in Docker.
The SDK calls `docker exec` directly. It does not run or require an HTTP API
service or a separate OAuth login.

## Requirements

- Python 3.10 or later on the host, with permission to run `docker exec`.
- A running BaiduNetdisk container, named `baidunetdisk` by default.
- This repository mounted in the container at `/config`, so that
  `/config/internal_api/inspector_eval.py` is available.
- The desktop client must be logged in and running inside the container.

Set `BAIDU_CONTAINER` if the container has another name. The default download
directory inside the container is `/config/baidunetdiskdownload`. If that is
mounted to a host directory other than `/home/sun/downloads`, set
`BAIDU_DOWNLOAD_ROOT` to the host path.

## Use

Add the repository directory to `PYTHONPATH`, then:

```python
from baidunetdisk import BaiduNetdisk

pan = BaiduNetdisk()
files = pan.list_files("/", refresh=True)
pan.download("/example.7z")
download_tasks = pan.tasks()
pan.upload("/path/under/mounted/repository/example.txt", "/backup")
```

`download` and `upload` queue transfers in the desktop client. Poll `tasks()`
and check the local file before treating a download as complete. Upload paths
must be visible inside the container under `/config` or its mounted download
directory. Remote listing uses one page per call; pass `page` and `page_size`
to retrieve additional pages.

Internally, the host invokes `docker exec -i <container> python3
/config/internal_api/inspector_eval.py`. That script connects to the Electron
main process's Node inspector and invokes its existing file-list and transfer
methods. If the inspector is inactive, the bridge enables it with `SIGUSR1`.
These are private desktop-client methods, so a client update may require an
SDK update.

Run the offline tests with `python3 -m unittest discover -s tests -v`.
