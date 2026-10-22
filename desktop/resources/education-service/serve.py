"""Private local backend. Credentials arrive through stdin, never command arguments."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    config = json.loads(sys.stdin.readline())
    state = Path(config['state']).resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.environ['REVIEW_DB_PATH'] = str(state / 'review.db')
    for name, folder in [('REFORMAT_DIR', 'reformat_jobs'), ('PPT_JOBS_DIR', 'ppt_jobs'),
                         ('INSPECT_DIR', 'compile_inspect'), ('COMPILE_JOBS_DIR', 'compile_jobs'),
                         ('TECTONIC_CACHE_DIR', 'tectonic-cache')]:
        os.environ['REVIEW_' + name] = str(state / folder)
    for name, value in config.get('llm', {}).items():
        if name in ('BASE_URL', 'API_KEY', 'MODEL'):
            os.environ['REVIEW_LLM_' + name] = value
    if config.get('tectonic'):
        os.environ['REVIEW_TECTONIC_PATH'] = config['tectonic']
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


if __name__ == '__main__':
    main()
