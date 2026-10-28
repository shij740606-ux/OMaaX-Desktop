"""Private local backend. Credentials arrive through stdin, never command arguments."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading


def configure_windows_runtime(resources):
    """Use the already bundled CRT before importing native education packages."""
    if os.name != 'nt' or not resources:
        return None
    root = Path(resources)
    try:
        lock = json.loads((root / 'windows-crt.lock.json').read_text(encoding='utf-8'))
        directory = root / lock['helperDirectory']
        for name, expected in lock['files'].items():
            content = (directory / name).read_bytes()
            if len(content) != expected['bytes'] or hashlib.sha256(content).hexdigest() != expected['sha256']:
                raise ValueError('Windows runtime verification failed')
        return os.add_dll_directory(str(directory.resolve()))
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError('The bundled education Windows runtime is incomplete. Repair or reinstall OMaaX.') from error


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    config = json.loads(sys.stdin.readline())
    # Keep this handle alive while the service and its background jobs are running.
    windows_runtime = configure_windows_runtime(config.get('windowsRuntime'))
    state = Path(config['state']).resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.environ['REVIEW_DB_PATH'] = str(state / 'review.db')
    for name, folder in [('PPT_JOBS_DIR', 'ppt_jobs')]:
        os.environ['REVIEW_' + name] = str(state / folder)
    for name, value in config.get('llm', {}).items():
        if name in ('BASE_URL', 'API_KEY', 'MODEL'):
            os.environ['REVIEW_LLM_' + name] = value
    from app.main import app
    from starlette.responses import JSONResponse
    import uvicorn
    token = config['token']

    @app.middleware('http')
    async def private_backend(request, call_next):
        if request.headers.get('origin') or not secrets.compare_digest(request.headers.get('x-omaax-backend', ''), token):
            return JSONResponse({'ok': False}, status_code=403)
        return await call_next(request)

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
    # Parent stdin closes even when the desktop is killed; no orphan service stays alive.
    def parent_closed():
        sys.stdin.read()
        server.should_exit = True
    threading.Thread(target=parent_closed, daemon=True).start()

    async def run():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not task.done():
            await asyncio.sleep(.05)
        if server.started:
            print(json.dumps({'port': sock.getsockname()[1]}), flush=True)
        await task
    asyncio.run(run())
    if windows_runtime is not None:
        windows_runtime.close()


if __name__ == '__main__':
    main()
