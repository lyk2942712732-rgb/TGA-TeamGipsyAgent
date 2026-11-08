"""Build legacy tools for the single slim Python, without a second runtime."""
import hashlib
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path('/build/vendor')
ROOT.mkdir(parents=True, exist_ok=True)
Path('/wheels').mkdir(exist_ok=True)


def source(name, url, sha256):
    with urllib.request.urlopen(url, timeout=180) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise RuntimeError(f'{name}: source digest mismatch')
    archive = ROOT / (name + '.tar.gz')
    archive.write_bytes(data)
    destination = ROOT / name
    destination.mkdir()
    with tarfile.open(archive) as handle:
        handle.extractall(destination, filter='data')
    return next(destination.iterdir())


wfuzz = source('wfuzz', 'https://files.pythonhosted.org/packages/c8/67/25c12b13cc63d2741fb64487c92d3ed98bac0646cf954c888ad66e88ecec/wfuzz-3.1.0.tar.gz',
               '0965f5968f5340ff0c54974164d51f9c08939e149bf2574e905d0df423789738')
setup = wfuzz / 'setup.py'
text = setup.read_text()
assert 'pyparsing>=2.4*' in text
setup.write_text(text.replace('pyparsing>=2.4*', 'pyparsing>=2.4'))

binwalk = source('binwalk', 'https://codeload.github.com/ReFirmLabs/binwalk/tar.gz/refs/tags/v2.3.4',
                 '60416bfec2390cec76742ce942737df3e6585c933c2467932f59c21e002ba7a9')
subprocess.run(['python', '/build/python-tool-compat.py', str(wfuzz), str(binwalk)], check=True)
for directory in [wfuzz, binwalk]:
    subprocess.run(['python', '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                    '--wheel-dir', '/wheels', str(directory)], check=True)
