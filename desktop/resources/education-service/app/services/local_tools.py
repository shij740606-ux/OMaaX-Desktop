"""Prepare the optional local TeX compiler on first compilation, without admin rights."""
import hashlib
import io
import os
from pathlib import Path
import platform
import tarfile
import threading
import time
import urllib.request
import zipfile

_LOCK = threading.Lock()
_ASSETS = {
    ('Windows', 'AMD64'): ('x86_64-pc-windows-msvc.zip', 'f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f'),
    ('Darwin', 'arm64'): ('aarch64-apple-darwin.tar.gz', 'a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667'),
    ('Darwin', 'x86_64'): ('x86_64-apple-darwin.tar.gz', '7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80'),
    ('Linux', 'x86_64'): ('x86_64-unknown-linux-musl.tar.gz', '8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7'),
}


def prepare_tectonic(packaged: Path, state: Path) -> Path:
    if packaged.is_file():
        return packaged
    target = state / 'tools' / ('tectonic.exe' if os.name == 'nt' else 'tectonic')
    with _LOCK:
        if target.is_file():
            return target
        asset = _ASSETS.get((platform.system(), platform.machine()))
        if not asset:
            raise RuntimeError('当前平台缺少本地 LaTeX 编译器')
        filename, expected = asset
        url = 'https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-' + filename
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        started = time.monotonic()
        data = bytearray()
        with opener.open(url, timeout=30) as response:
            while chunk := response.read(65536):
                data.extend(chunk)
                if len(data) > 50 * 1024 * 1024 or time.monotonic() - started > 240:
                    raise RuntimeError('本地编译器下载超时，请稍后重试')
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('本地编译器校验失败，请重试')
        # Extract one known regular file only; never unpack arbitrary archive paths.
        if filename.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = [n for n in archive.namelist() if Path(n).name == target.name]
                if len(names) != 1: raise RuntimeError('编译器文件缺失')
                binary = archive.read(names[0])
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                files = [m for m in archive.getmembers() if m.isfile() and Path(m.name).name == target.name]
                if len(files) != 1: raise RuntimeError('编译器文件缺失')
                binary = archive.extractfile(files[0]).read()
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_suffix('.pending')
        pending.write_bytes(binary)
        pending.chmod(0o700)
        pending.replace(target)
        return target
