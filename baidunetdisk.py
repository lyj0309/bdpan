"""Python SDK for the logged-in BaiduNetdisk desktop client."""

from os import PathLike
from typing import Iterable, Union

from internal_api.desktop_bridge import download as _download
from internal_api.desktop_bridge import list_files as _list_files
from internal_api.desktop_bridge import tasks as _tasks
from internal_api.desktop_bridge import upload as _upload


PathValue = Union[str, PathLike]


def _paths(value: Union[PathValue, Iterable[PathValue]]):
    if isinstance(value, (str, PathLike)):
        return [str(value)]
    return [str(path) for path in value]


class BaiduNetdisk:
    """Call the native API of the currently logged-in desktop client."""

    def __init__(self, cid="0", download_dir="/config/baidunetdiskdownload"):
        self.cid = str(cid)
        self.download_dir = str(download_dir)

    def list_files(
        self,
        path="/",
        *,
        page=1,
        page_size=1000,
        sort_type=0,
        descending=True,
        refresh=False,
    ):
        """Return files directly under a remote directory."""
        return _list_files(
            path=str(path),
            page=page,
            page_size=page_size,
            sort_type=sort_type,
            desc=descending,
            refresh=refresh,
            cid=self.cid,
        )

    def upload(self, local_paths, remote_dir="/"):
        """Upload one or more mounted local paths to a remote directory."""
        return _upload(_paths(local_paths), str(remote_dir), self.cid)

    def download(self, remote_paths, local_dir=None, *, refresh=False):
        """Download one or more remote paths through the native downloader."""
        return _download(
            _paths(remote_paths),
            str(local_dir or self.download_dir),
            self.cid,
            refresh,
        )

    def tasks(self, kind="download"):
        """Return current upload or download tasks."""
        return _tasks(kind, self.cid)


__all__ = ["BaiduNetdisk"]
