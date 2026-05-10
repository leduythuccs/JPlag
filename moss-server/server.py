#!/usr/bin/env python3
"""MOSS-compatible TCP server that runs JPlag and serves results via a shared web viewer."""

import argparse
import http.server
import logging
import mimetypes
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MOSS_TO_JPLAG = {
    'c': 'c',
    'cc': 'cpp',
    'java': 'java',
    'python': 'python3',
    'csharp': 'csharp',
    'javascript': 'javascript',
}

JPLAG_EXT = {
    'c': '.c',
    'cpp': '.cpp',
    'java': '.java',
    'python3': '.py',
    'csharp': '.cs',
    'javascript': '.js',
}


@dataclass
class Config:
    bind: str = '0.0.0.0'
    public_host: str = 'localhost'
    moss_port: int = 7690
    http_port: int = 8080
    jplag_jar: str = '../cli/target/jplag-6.3.0-jar-with-dependencies.jar'
    viewer_dir: str = '../report-viewer/report-viewer/dist'
    work_dir: str = '/tmp/jplag-moss-sessions'
    java: str = 'java'
    allowed_users: frozenset = None  # None = allow all


def parse_args() -> Config:
    default_config = Config()
    p = argparse.ArgumentParser(description='MOSS-compatible server backed by JPlag')
    p.add_argument('--bind', default=default_config.bind)
    p.add_argument('--public-host', default=default_config.public_host, dest='public_host')
    p.add_argument('--moss-port', type=int, default=default_config.moss_port, dest='moss_port')
    p.add_argument('--http-port', type=int, default=default_config.http_port, dest='http_port')
    p.add_argument('--jplag-jar', default=default_config.jplag_jar, dest='jplag_jar')
    p.add_argument('--viewer-dir', default=default_config.viewer_dir, dest='viewer_dir')
    p.add_argument('--work-dir', default=default_config.work_dir, dest='work_dir')
    p.add_argument('--java', default=default_config.java)
    p.add_argument('--log-level', default='INFO', dest='log_level',
                   choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    p.add_argument(
        '--allowed-users', default=None, dest='allowed_users',
        help='Comma-separated list of allowed MOSS user IDs. If omitted, all IDs are accepted.'
    )
    args = p.parse_args()
    allowed: frozenset | None = None
    if args.allowed_users:
        allowed = frozenset(int(uid.strip()) for uid in args.allowed_users.split(',') if uid.strip())
    cfg = Config(
        bind=args.bind,
        public_host=args.public_host,
        moss_port=args.moss_port,
        http_port=args.http_port,
        jplag_jar=args.jplag_jar,
        viewer_dir=args.viewer_dir,
        work_dir=args.work_dir,
        java=args.java,
        allowed_users=allowed,
    )
    logging.basicConfig(level=getattr(logging, args.log_level),
                        format='%(asctime)s %(levelname)s %(message)s')
    return cfg


# ---------------------------------------------------------------------------
# HTTP server — serves viewer static files and .jplag result files
# ---------------------------------------------------------------------------

def make_viewer_handler(config: Config):
    """Return a request handler class closed over config."""
    viewer_dir = Path(config.viewer_dir)
    results_dir = Path(config.work_dir) / 'results'

    class ViewerHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug('HTTP %s - ' + fmt, self.address_string(), *args)

        def do_GET(self):
            path = self.path.split('?', 1)[0]  # strip query string for routing

            if path.startswith('/results/'):
                name = path[len('/results/'):]
                # Basic sanitization: no path traversal
                if '/' in name or name.startswith('.'):
                    self._send(400, b'Bad request')
                    return
                file_path = results_dir / name
                if not file_path.exists():
                    self._send(404, b'Not found')
                    return
                data = file_path.read_bytes()
                self._send(200, data, 'application/zip')
                return

            # Serve viewer static files
            if path == '/' or path == '':
                static_path = viewer_dir / 'index.html'
            else:
                static_path = viewer_dir / path.lstrip('/')

            if not static_path.exists() or not static_path.is_file():
                # Fall back to index.html for SPA client-side routing
                static_path = viewer_dir / 'index.html'

            if not static_path.exists():
                self._send(404, b'Viewer not built. Run: cd JPlag/report-viewer/report-viewer && npm install && npm run build')
                return

            mime, _ = mimetypes.guess_type(str(static_path))
            data = static_path.read_bytes()
            self._send(200, data, mime or 'application/octet-stream')

        def _send(self, code: int, body: bytes, content_type: str = 'text/plain'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

    return ViewerHandler


def start_http_server(config: Config):
    handler = make_viewer_handler(config)
    # Allow reuse so restarts don't hit "address already in use"
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer((config.bind, config.http_port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name='http-server')
    t.start()
    log.info('HTTP server listening on %s:%d', config.bind, config.http_port)


# ---------------------------------------------------------------------------
# MOSS session — one per TCP connection
# ---------------------------------------------------------------------------

@dataclass
class _StagedFile:
    file_id: int
    language: str
    display_name: str
    content: bytes


class MossSession:
    def __init__(self, conn, addr, config: Config):
        self._conn = conn
        self._addr = addr
        self._config = config
        self._buf = bytearray()
        self._base_files: list[_StagedFile] = []
        self._submission_files: list[_StagedFile] = []

    # ------------------------------------------------------------------
    # Low-level buffered I/O
    # ------------------------------------------------------------------

    def _readline(self) -> bytes:
        while b'\n' not in self._buf:
            chunk = self._conn.recv(4096)
            if not chunk:
                raise ConnectionError('client disconnected')
            self._buf.extend(chunk)
        idx = self._buf.index(b'\n')
        line = bytes(self._buf[:idx + 1])
        del self._buf[:idx + 1]
        return line

    def _readexact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._conn.recv(4096)
            if not chunk:
                raise ConnectionError('client disconnected mid-file')
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def _readline_str(self) -> str:
        return self._readline().decode('utf-8', errors='replace').strip()

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def run(self):
        client = f'{self._addr[0]}:{self._addr[1]}'
        log.info('New MOSS connection from %s', client)
        try:
            self._handshake()
            self._receive_files()
            self._process()
        except ConnectionError as e:
            log.warning('%s disconnected: %s', client, e)
        except Exception:
            log.exception('Error handling connection from %s', client)
        finally:
            try:
                self._conn.close()
            except OSError:
                pass
            log.info('Connection from %s closed', client)

    def _handshake(self):
        line = self._readline_str()
        if not line.startswith('moss '):
            raise ValueError(f'Expected "moss <user_id>", got: {line!r}')
        self._user_id = int(line.split()[1])
        if self._config.allowed_users is not None and self._user_id not in self._config.allowed_users:
            log.warning('Rejected user_id=%d (not in whitelist)', self._user_id)
            raise PermissionError(f'user_id {self._user_id} is not whitelisted')

        line = self._readline_str()
        self._directory = line.split()[1] == '1'

        self._readline_str()  # X (experimental) — ignored

        line = self._readline_str()
        self._maxmatches = int(line.split()[1])

        line = self._readline_str()
        self._show = int(line.split()[1])

        line = self._readline_str()
        self._moss_language = line.split()[1]
        self._jplag_language = MOSS_TO_JPLAG.get(self._moss_language, 'text')
        log.info('Language: MOSS=%s → JPlag=%s', self._moss_language, self._jplag_language)

        self._conn.sendall(b'yes\n')

    def _receive_files(self):
        while True:
            line = self._readline_str()
            if line.startswith('query '):
                # Extract comment (everything after "query 0 ")
                parts = line.split(' ', 2)
                self._comment = parts[2] if len(parts) > 2 else ''
                break
            if line.startswith('end'):
                raise ConnectionError('client sent "end" before query')
            if line.startswith('file '):
                # file <id> <lang> <size> <display_name>
                parts = line.split(' ', 4)
                file_id = int(parts[1])
                lang = parts[2]
                size = int(parts[3])
                display_name = parts[4] if len(parts) > 4 else ''
                content = self._readexact(size)
                staged = _StagedFile(file_id, lang, display_name, content)
                if file_id == 0:
                    self._base_files.append(staged)
                else:
                    self._submission_files.append(staged)
                log.debug('Received file id=%d name=%r size=%d', file_id, display_name, size)

    def _process(self):
        if not self._submission_files:
            log.warning('No submission files received, closing connection')
            return

        session_dir = Path(self._config.work_dir) / 'sessions' / uuid.uuid4().hex
        session_dir.mkdir(parents=True)
        log.info('Session dir: %s', session_dir)

        try:
            self._write_files(session_dir)
            result_path = self._run_jplag(session_dir)
            if result_path is None:
                return
            url = self._publish_result(result_path)
            self._conn.sendall(url.encode() + b'\n')
            log.info('Result URL: %s', url)
        except Exception:
            log.exception('Failed to process session')

        # Drain the "end\n" line the client sends after receiving the URL
        try:
            self._readline()
        except ConnectionError:
            pass

    # ------------------------------------------------------------------
    # File system reconstruction
    # ------------------------------------------------------------------

    def _sanitize_part(self, part: str) -> str:
        """Strip dangerous path components."""
        safe = Path(part).name  # take only the final component, no dirs
        return safe if safe not in ('', '.', '..') else '_'

    def _write_files(self, session_dir: Path):
        submissions_dir = session_dir / 'submissions'
        for f in self._submission_files:
            name = f.display_name.replace('\\', '/')
            student, sep, filename = name.partition('/')
            if not sep:
                # No slash: use full name as both student dir and filename
                filename = student
            student = self._sanitize_part(student)
            filename = filename.lstrip('/') or 'file'
            # Strip path traversal from filename parts
            safe_parts = [self._sanitize_part(p) for p in Path(filename).parts]
            dest = submissions_dir / (student + JPLAG_EXT.get(self._jplag_language, ''))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(f.content)
            log.debug('Wrote submission: %s', dest.relative_to(session_dir))

        if self._base_files:
            base_dir = session_dir / 'base'
            base_dir.mkdir()
            for f in self._base_files:
                safe_name = self._sanitize_part(Path(f.display_name).name or 'base')
                dest = base_dir / safe_name
                dest.write_bytes(f.content)
                log.debug('Wrote base file: %s', dest.relative_to(session_dir))

    # ------------------------------------------------------------------
    # JPlag execution
    # ------------------------------------------------------------------

    def _run_jplag(self, session_dir: Path) -> Path | None:
        jar = self._config.jplag_jar
        if not Path(jar).exists():
            log.error('JPlag JAR not found: %s', jar)
            log.error('Build it with: cd JPlag && mvn clean package assembly:single')
            return None

        result_base = session_dir / 'result'
        cmd = [
            self._config.java, '-jar', jar,
            '-l', self._jplag_language,
            '-r', str(result_base),
            '-M', 'RUN',
            '--overwrite',
            str(session_dir / 'submissions'),
        ]
        if (session_dir / 'base').exists():
            cmd += ['--bc', str(session_dir / 'base')]

        log.info('Running JPlag: %s', ' '.join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            log.error('JPlag timed out after 300s')
            return None

        if proc.returncode != 0:
            log.error('JPlag failed (exit %d):\n%s', proc.returncode,
                      proc.stderr.decode('utf-8', errors='replace'))
            return None

        result_file = result_base.with_suffix('.jplag')
        if not result_file.exists():
            log.error('JPlag succeeded but result file not found: %s', result_file)
            return None

        log.info('JPlag completed successfully: %s', result_file)
        return result_file

    # ------------------------------------------------------------------
    # Result publishing
    # ------------------------------------------------------------------

    def _publish_result(self, result_path: Path) -> str:
        session_id = uuid.uuid4().hex
        results_dir = Path(self._config.work_dir) / 'results'
        results_dir.mkdir(parents=True, exist_ok=True)
        dest = results_dir / f'{session_id}.jplag'
        shutil.move(str(result_path), str(dest))
        log.info('Result published: %s', dest)

        host = self._resolve_public_host()
        return f'http://{host}:{self._config.http_port}/?file=/results/{session_id}.jplag'

    def _resolve_public_host(self) -> str:
        host = self._config.public_host
        if host not in ('0.0.0.0', '::'):
            return host
        try:
            local = self._conn.getsockname()[0]
            if local not in ('0.0.0.0', '', '::'):
                return local
        except OSError:
            pass
        return 'localhost'


# ---------------------------------------------------------------------------
# MOSS TCP server
# ---------------------------------------------------------------------------

class MossServer:
    def __init__(self, config: Config):
        self._config = config

    def start(self):
        sock = __import__('socket').socket()
        sock.setsockopt(__import__('socket').SOL_SOCKET, __import__('socket').SO_REUSEADDR, 1)
        sock.bind((self._config.bind, self._config.moss_port))
        sock.listen(128)
        log.info('MOSS server listening on %s:%d', self._config.bind, self._config.moss_port)
        while True:
            conn, addr = sock.accept()
            t = threading.Thread(
                target=self._handle,
                args=(conn, addr),
                daemon=True,
            )
            t.start()

    def _handle(self, conn, addr):
        MossSession(conn, addr, self._config).run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    config = parse_args()

    jar = Path(config.jplag_jar)
    if not jar.exists():
        log.warning('JPlag JAR not found at %s', jar)
        log.warning('Build it with: cd JPlag && mvn clean package assembly:single')

    viewer = Path(config.viewer_dir)
    if not viewer.exists():
        log.warning('Viewer dist not found at %s', viewer)
        log.warning('Build it with: cd JPlag/report-viewer/report-viewer && npm install && npm run build')

    Path(config.work_dir, 'results').mkdir(parents=True, exist_ok=True)
    Path(config.work_dir, 'sessions').mkdir(parents=True, exist_ok=True)

    start_http_server(config)
    MossServer(config).start()


if __name__ == '__main__':
    main()
