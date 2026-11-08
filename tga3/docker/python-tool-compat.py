"""Small, explicit Python 3.12 ports for the pinned legacy security tools."""
import sys
from pathlib import Path

LOADER = '''from importlib.util import spec_from_file_location, module_from_spec
import sys

def load_source(name, path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
'''


def wfuzz_compat(root):
    paths = list(root.rglob('moduleman/loader.py'))
    assert len(paths) == 1, paths
    path = paths[0]
    text = path.read_text()
    before = ('exten_file, filename, description = imp.find_module(fn, [dirname])\n'
              '            module = imp.load_module(fn, exten_file, filename, description)')
    assert 'import imp\n' in text and before in text
    text = text.replace('import imp\n', LOADER)
    path.write_text(text.replace(before, 'filename = os.path.join(dirname, fn + ".py")\n'
                                '            module = load_source(fn, filename)'))


def binwalk_compat(root):
    paths = list(root.rglob('core/plugin.py'))
    assert len(paths) == 1, paths
    path = paths[0]
    text = path.read_text()
    # A source build has imp, an installed diagnostic image may already be ported.
    if 'import imp\n' in text:
        path.write_text(text.replace('import imp\n', LOADER).replace('imp.load_source(', 'load_source('))
    else:
        assert 'spec_from_file_location' in text
    module = path.with_name('module.py')
    text = module.read_text()
    assert '        import imp\n' in text
    module.write_text(text.replace('        import imp\n',
                                   '        from binwalk.core.plugin import load_source\n')
                      .replace('imp.load_source(', 'load_source('))


if __name__ == '__main__':
    wfuzz_compat(Path(sys.argv[1]))
    binwalk_compat(Path(sys.argv[2]))
