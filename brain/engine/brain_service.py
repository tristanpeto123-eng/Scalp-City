"""Persistent read-only Project OS Brain scanning service.

Monitors an explicitly mounted Council filesystem. This service is *not*
an independent agent, a Council worker, or authority to change monitored jobs.
No source records are modified. It writes only its separate Brain ledger.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import fcntl
from .brain import snapshot, save_snapshot


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


@dataclass(frozen=True)
class Config:
    council: Path
    ledger: Path
    archivist: Path | None = None
    registers: Path | None = None
    interval: int = 60
    stale_after: int = 180
    host: str = '127.0.0.1'
    port: int = 8080
    status_token: str | None = None

    def validate(self):
        if self.interval < 5 or self.stale_after <= self.interval:
            raise ValueError('Invalid scan interval/staleness deadline')
        if self.port < 0 or self.port > 65535:
            raise ValueError('Invalid port')
        root = self.council.resolve()
        ledger = self.ledger.resolve()
        if ledger == root or root in ledger.parents:
            raise ValueError('Brain ledger cannot live inside Council source tree')
        if self.archivist and ledger == self.archivist.resolve():
            raise ValueError('Brain ledger must not overwrite Archivist')
        if self.status_token is not None and len(self.status_token) < 24:
            raise ValueError('Status token too short')
        return self


class ScanState:
    def __init__(self, config: Config):
        self.config = config.validate()
        self._lock = threading.Lock()
        self._last = None
        self._last_success_monotonic = None
        self._error = None
        self._count = 0

    def once(self):
        try:
            report = snapshot(self.config.council, self.config.archivist, registers_root=self.config.registers)
            save_snapshot(self.config.ledger, report)
        except Exception as exc:
            # The error reason stays local and is not leaked on public health endpoint.
            with self._lock:
                self._error = f'{type(exc).__name__}: {exc}'
            return False
        with self._lock:
            self._last = report
            self._last_success_monotonic = time.monotonic()
            self._error = None
            self._count += 1
        return True

    def health(self):
        with self._lock:
            fresh = (self._last_success_monotonic is not None and
                     0 <= time.monotonic() - self._last_success_monotonic <= self.config.stale_after)
            sources = list((self._last or {}).get('sources') or [])
            source_connected = any(s['source_id'] == 'council' and s['status'] == 'READ' for s in sources)
            # Health requires an actual read, but does not imply global inventory completeness.
            ready = fresh and self._error is None and source_connected
            return {'service': 'brain','scanner': 'scanning' if ready else 'degraded',
                    'snapshot_fresh': bool(fresh), 'council_readable': bool(source_connected)}, 200 if ready else 503

    def status(self):
        with self._lock:
            return {'service': 'brain', 'checked_at': now_utc(), 'scans_persisted_this_process': self._count,
                    'latest_scan': self._last, 'error': self._error,
                    'observation_mode': 'PERSISTENT_MONITORING' if self._last_success_monotonic else 'STARTING',
                    'note': 'Process presence and source snapshot do not establish independent worker connections'}

    def loop(self, stop):
        while not stop.wait(self.config.interval):
            self.once()


def handler_factory(state: ScanState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/livez':
                data, code = {'service': 'brain', 'process': 'alive'}, 200
            elif self.path == '/healthz':
                data, code = state.health()
            elif self.path == '/status':
                token = state.config.status_token
                if not token:
                    data, code = {'error': 'status endpoint disabled'}, 404
                else:
                    import hmac
                    actual = self.headers.get('Authorization', '')
                    good = hmac.compare_digest(actual.encode(), ('Bearer '+token).encode())
                    data, code = (state.status(), 200) if good else ({'error':'unauthorized'},401)
            else:
                data, code = {'error': 'not found'}, 404
            raw = json.dumps(data, sort_keys=True).encode()
            self.send_response(code)
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            # Do not log bearer token or task payload from request headers.
            pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--council-root', default=os.getenv('BRAIN_COUNCIL_ROOT','/state/council'))
    parser.add_argument('--archivist-db', default=os.getenv('BRAIN_ARCHIVIST_DB'))
    parser.add_argument('--ledger', default=os.getenv('BRAIN_LEDGER','/state/brain/scans.sqlite3'))
    parser.add_argument('--registers-root', default=os.getenv('BRAIN_REGISTERS_ROOT'))
    parser.add_argument('--interval', type=int, default=int(os.getenv('BRAIN_SCAN_INTERVAL','60')))
    parser.add_argument('--stale-after', type=int, default=int(os.getenv('BRAIN_STALE_AFTER','180')))
    parser.add_argument('--host', default=os.getenv('BRAIN_BIND','0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT','8080')))
    args = parser.parse_args(argv)
    config = Config(Path(args.council_root),Path(args.ledger),
                    Path(args.archivist_db) if args.archivist_db else None,
                    Path(args.registers_root) if args.registers_root else None,
                    args.interval,args.stale_after,args.host,args.port,
                    os.getenv('BRAIN_STATUS_TOKEN')).validate()
    config.ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config.ledger.parent, 0o700)
    # Advisory lock avoids concurrent scan writers sharing the same journal.
    with (config.ledger.parent/'brain.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        state = ScanState(config)
        state.once()
        stop = threading.Event()
        scanner = threading.Thread(target=state.loop,args=(stop,),daemon=True,name='brain-scanner')
        with ThreadingHTTPServer((config.host,config.port), handler_factory(state)) as server:
            scanner.start()
            print(json.dumps({'event':'BRAIN_SERVICE_STARTED','at':now_utc(),
                              'council_scope':str(config.council),'interval_seconds':config.interval}),flush=True)
            try: server.serve_forever(poll_interval=0.3)
            except KeyboardInterrupt: pass
            finally: stop.set();scanner.join(timeout=2)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
