"""Executable integration checks for the unified image (run inside container)."""
import gzip
import importlib
import importlib.metadata as metadata
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (args, result.stdout[-1500:], result.stderr[-1500:])
    return result.stdout + result.stderr


commands = '''curl wget git jq rg vim.tiny less ps ip ping gcc g++ make nasm pkg-config
unzip 7z xz file xxd strings readelf strace ltrace tmux openssl nmap socat nc gdb php ruby
mysql redis-cli psql smbclient exiftool ncat gdbserver gobuster dirb hydra john sslscan
foremost steghide ffuf masscan whatweb tshark sqlmap wfuzz wafw00f hashid binwalk pwn
ROPgadget ropper checksec smbclient.py'''.split()
for command in commands:
    assert shutil.which(command), f'missing required worker command: {command}'
assert sys.version_info[:2] == (3, 12)
assert sys.prefix == sys.base_prefix, 'unexpected virtual environment'
assert not Path('/opt/tga3-venv').exists()
for shell in ['-c', '-lc']:
    value = json.loads(run(['bash', shell, 'python3 -c "import json,sys,os; print(json.dumps([os.path.realpath(sys.executable),sys.prefix]))"']))
    assert value == [os.path.realpath(sys.executable), sys.prefix], (shell, value)
assert Path('/usr/bin/python3').resolve() == Path(sys.executable).resolve()
assert not list(Path('/usr/bin').glob('python3.[0-9]*'))
names = [re.sub(r'[-_.]+', '-', d.metadata['Name']).lower() for d in metadata.distributions()]
assert len(names) == len(set(names)), 'duplicate distribution metadata'
assert not {'asyncpg', 'fastapi', 'uvloop', 'httptools', 'watchfiles'} & set(names)
assert not Path('/opt/tga3/src').exists() and not Path('/opt/tga3/build').exists()
if importlib.util.find_spec('tga3'):
    # Check the image as shipped, before importing project modules below.
    assert sys.dont_write_bytecode, 'runtime bytecode writes must stay disabled'
    stdlib = Path(sysconfig.get_path('stdlib'))
    for source in ['http/client.py', 'json/__init__.py', 'ssl.py', 'email/message.py']:
        cache = Path(importlib.util.cache_from_source(str(stdlib / source)))
        assert cache.is_file(), f'missing build-time hook bytecode: {source}'
        assert cache.read_bytes()[:4] == importlib.util.MAGIC_NUMBER, cache
if os.getuid() == 1000:
    assert os.access(sysconfig.get_path('purelib'), os.W_OK)
    assert os.access('/usr/local/bin', os.W_OK)
for module in ['pwn', 'Crypto', 'Cryptodome', 'gmpy2', 'z3', 'ropper', 'capstone',
               'sympy', 'PIL', 'bs4', 'impacket', 'wfuzz', 'binwalk',
               'httpx', 'websockets', 'mcp', 'pydantic', 'yaml']:
    importlib.import_module(module)
import gmpy2
import sympy
import z3
from PIL import Image
from bs4 import BeautifulSoup
from pwn import asm, disasm, context
assert gmpy2.invert(3, 7) == 5
assert sympy.factorint(91) == {7: 1, 13: 1}
assert z3.simplify(z3.IntVal(1) + 2).as_long() == 3
assert Image.new('RGB', (2, 2)).size == (2, 2)
assert BeautifulSoup('<b>ok</b>', 'html.parser').b.text == 'ok'
context.clear(arch='amd64')
assert asm('nop') == b'\x90' and 'nop' in disasm(b'\x90')
run(['python', '-m', 'pip', 'check'])
run(['gdb', '-q', '-batch', '-ex', 'python print("gdb-python-ok")'])
for args in [['sqlmap', '--version'], ['wfuzz', '-h'], ['wafw00f', '-V'],
             ['hashid', '-h'], ['smbclient.py', '-h'], ['ropper', '--version'], ['binwalk', '--help']]:
    run(args)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    # Native CLI transient helper directories must survive bash -lc as well.
    helper = root / 'image-path-sentinel'
    helper.write_text('#!/bin/sh\nexit 0\n')
    helper.chmod(0o755)
    env = {**os.environ, 'PATH': str(root) + ':' + os.environ['PATH']}
    assert subprocess.check_output(['bash', '-lc', 'command -v image-path-sentinel'], env=env, text=True).strip() == str(helper)
    if os.getuid() == 1000:
        wheel = root / 'image_env_probe-0.0.0-py3-none-any.whl'
        content = {
            'image_env_probe.py': 'VALUE = 1\n',
            'image_env_probe-0.0.0.dist-info/METADATA': 'Metadata-Version: 2.1\nName: image-env-probe\nVersion: 0.0.0\n',
            'image_env_probe-0.0.0.dist-info/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        }
        with zipfile.ZipFile(wheel, 'w') as archive:
            for name, data in content.items():
                archive.writestr(name, data)
            archive.writestr('image_env_probe-0.0.0.dist-info/RECORD',
                             ''.join(name + ',,\n' for name in content))
        run(['python', '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)])
        value = run(['python', '-c', 'import image_env_probe; print(image_env_probe.__file__)'])
        assert value.strip().startswith('/usr/local/lib/python3.12/site-packages/'), value
        run(['python', '-m', 'pip', 'uninstall', '-y', 'image-env-probe'])
    sample = root / 'sample.gz'
    sample.write_bytes(gzip.compress(b'unified-image-probe' * 100, mtime=0))
    assert 'gzip compressed data' in run(['binwalk', str(sample)]).lower()
    # Actually compile and inspect a binary, rather than only checking imports.
    (root / 'probe.c').write_text('int main(void) { return 0; }')
    run(['gcc', str(root / 'probe.c'), '-o', str(root / 'probe')])
    run([str(root / 'probe')])
    run(['checksec', str(root / 'probe')])
    run(['ROPgadget', '--binary', str(root / 'probe'), '--only', 'ret'])
    run(['ropper', '--file', str(root / 'probe'), '--search', 'ret'])


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'unified-http-probe')


server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    output = run(['wfuzz', '-z', 'list,probe', '-u', f'http://127.0.0.1:{server.server_port}/FUZZ'])
    assert 'probe' in output and '200' in output, output
finally:
    server.shutdown()
    server.server_close()
for module in ['tga3.workers.openai_worker', 'tga3.workers.claude_worker',
               'tga3.workers.hook_service', 'tga3.knowledge', 'tga3.workers.aoe_scout']:
    # The safety-tool base has no project wheel yet.
    if importlib.util.find_spec('tga3'):
        importlib.import_module(module)
print(json.dumps({'passed': True, 'uid': os.getuid(), 'python': sys.version.split()[0],
                  'site_packages': next(d for d in sys.path if d.endswith('site-packages')),
                  'distributions': len(names), 'commands': len(commands)}))
