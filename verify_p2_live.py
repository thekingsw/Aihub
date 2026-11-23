"""Live same-key check. Never persist or print credential values."""
import sys
sys.dont_write_bytecode = True
import os, re, json, tomllib, hashlib, tempfile, threading, logging
from pathlib import Path
from http.client import HTTPConnection

ROOT = Path(__file__).resolve().parent
results = {}

def run():
    # Credentials remain in memory/environment only.
    start = (ROOT / 'start.bat').read_text(encoding='utf-8-sig')
    match = re.search(r'^\s*set\s+AI_HUB_INTERNAL_TOKEN=(.+?)\s*$', start, re.M | re.I)
    if not match:
        raise RuntimeError('internal_token_not_found')
    os.environ['TINKERAPP_INTERNAL_TOKEN'] = match.group(1).strip()
    config = Path.home() / '.codex' / 'config.toml'
    original = config.read_bytes()
    key = tomllib.loads(original.decode('utf-8-sig'))['model_providers']['OpenAI']['experimental_bearer_token']
    backups = set(config.parent.glob(config.name + '.bak.*'))
    stamp = config.stat().st_mtime_ns
    results['key_length'] = len(key)
    results['before_backup_count'] = len(backups)
    sys.path.insert(0, r'E:\MyProj\tinkerapp\src')
    with tempfile.TemporaryDirectory(prefix='p2-live-', dir=ROOT) as temporary:
        os.environ['TINKERAPP_DATA_DIR'] = temporary
        import app, auth, db
        logging.disable(logging.CRITICAL)
        path = Path(temporary) / 'tinkerapp.sqlite3'
        db.init_db(path)
        # Create an ephemeral authenticated owner session, as in product tests.
        import secrets
        user = db.create_user(path, 'live-check-owner', auth.hash_password(secrets.token_urlsafe(32)), role='owner')
        session, _ = auth.create_session(user, False, 'live-check', '127.0.0.1', path)
        csrf = auth.csrf_token_for(session, path=path)
        observed = []
        class ObservedConnection(HTTPConnection):
            def getresponse(self):
                response = super().getresponse()
                # Observe real upstream body; handler itself only uses status.
                payload = json.loads(response.read())
                observed.append({'host': self.host, 'port': self.port,
                                 'status': response.status,
                                 'key_length': payload.get('key_length'),
                                 'changed': payload.get('changed'),
                                 'backup_present': bool(payload.get('backup'))})
                return response
        app.HTTPConnection = ObservedConnection
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TinkerHandler)
        server.app_config = {'db_path': path}
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        results['calls'] = []
        try:
            for index in range(2):
                connection = HTTPConnection(*server.server_address, timeout=45)
                try:
                    connection.request('POST', app.KEY_ROTATION_PATH,
                                       json.dumps({'api_key': key}),
                                       {'Content-Type': 'application/json',
                                        'Cookie': 'tinker_session=' + session,
                                        'X-CSRF-Token': csrf})
                    response = connection.getresponse()
                    body = response.read()
                    payload = json.loads(body)
                    current = config.read_bytes()
                    result = {'attempt': index + 1, 'status': response.status,
                              'key_length': payload.get('key_length'),
                              'ok': payload.get('ok'),
                              'config_bytes_unchanged': current == original,
                              'mtime_unchanged': config.stat().st_mtime_ns == stamp,
                              'key_unchanged': tomllib.loads(current.decode('utf-8-sig'))['model_providers']['OpenAI']['experimental_bearer_token'] == key,
                              'backup_set_unchanged': set(config.parent.glob(config.name + '.bak.*')) == backups,
                              'credentials_absent_in_response': key.encode() not in body and os.environ['TINKERAPP_INTERNAL_TOKEN'].encode() not in body}
                    results['calls'].append(result)
                finally:
                    connection.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        results['upstream'] = observed
        results['passed'] = len(observed) == 2 and all(
            c['status'] == 200 and c['ok'] is True and c['key_length'] == len(key)
            and all(c[name] for name in ('config_bytes_unchanged', 'mtime_unchanged', 'key_unchanged', 'backup_set_unchanged', 'credentials_absent_in_response'))
            for c in results['calls']) and all(
                c['status'] == 200 and c['key_length'] == len(key) and c['changed'] is False
                and c['host'] == '100.79.204.50' and c['port'] == 8090 for c in observed)

try:
    run()
except Exception as exc:
    results['error_type'] = type(exc).__name__
    results['passed'] = False
output = json.dumps(results, ensure_ascii=True, indent=2)
(ROOT / 'p2-live-evidence.json').write_text(output + '\n', encoding='utf-8')
print(output)
sys.exit(0 if results.get('passed') else 1)
