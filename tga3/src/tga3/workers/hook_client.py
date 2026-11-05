"""Standard-library-only native hook client; all heavy work stays in the Worker."""

import http.client
import json
import os
import sys


def main():
    try:
        event = json.load(sys.stdin)
        event['_tga_generation'] = os.environ.get('TGA3_CLI_GENERATION', '')
        connection = http.client.HTTPConnection('127.0.0.1', int(os.environ['TGA3_HOOK_PORT']), timeout=14)
        try:
            connection.request('POST', '/', json.dumps(event), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(result.get('error', 'Hook service failed'))
            print(json.dumps(result, ensure_ascii=False))
        finally:
            connection.close()
    except Exception as exc:
        print(f'TGA hook failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == '__main__':
    main()
