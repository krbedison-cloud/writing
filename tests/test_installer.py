"""Exercise installer port changes without touching Docker or the network."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

INSTALLER = Path(__file__).resolve().parents[1] / 'install.sh'

class InstallerPortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.install = self.root / 'installation'
        self.install.mkdir()
        docker = self.bin / 'docker'
        docker.write_text('''#!/bin/sh
case "$1" in
  info|pull) exit 0 ;;
  compose)
    case " $* " in
      *" port "*) printf '127.0.0.1:%s\\n' "${APP_PORT:-18086}" ;;
    esac
    exit 0 ;;
  *) exit 2 ;;
esac
''')
        curl = self.bin / 'curl'
        curl.write_text('''#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then
    shift
    printf 'services: {}\\n' > "$1"
    exit 0
  fi
  shift
done
exit 1
''')
        docker.chmod(0o755)
        curl.chmod(0o755)
    def tearDown(self):
        self.temp.cleanup()
    def run_installer(self, port=None, extra=None):
        env=dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'], SHENLUN_DIR=str(self.install))
        for name in ['SHENLUN_VERSION','SHENLUN_PORT','APP_PORT']:
            env.pop(name,None)
        if port is not None:
            env['SHENLUN_PORT']=port
        env.update(extra or {})
        return subprocess.run(['bash',str(INSTALLER)],env=env,text=True,capture_output=True)
    def test_new_install_uses_18086(self):
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('APP_PORT=18086\n',(self.install/'.env').read_text())
    def test_existing_configuration_is_preserved_without_explicit_request(self):
        original='APP_PORT=15222\nAPP_PASSWORD=example-only\n# custom setting\nCUSTOM_FLAG=keep\n'
        (self.install/'.env').write_text(original)
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.install/'.env').read_text(),original)
    def test_explicit_port_replaces_old_port_and_preserves_other_settings(self):
        original='APP_PORT=8080\nAPP_PASSWORD=example-only\n# custom setting\nCUSTOM_FLAG=keep\n'
        (self.install/'.env').write_text(original)
        result=self.run_installer('18086',{'APP_PORT':'8080'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.install/'.env').read_text(),original.replace('APP_PORT=8080','APP_PORT=18086'))
        self.assertIn('http://localhost:18086',result.stdout)
    def test_invalid_port_does_not_change_existing_config(self):
        original='APP_PORT=8080\nAPP_PASSWORD=example-only\n'
        (self.install/'.env').write_text(original)
        for port in ['0','65536','abc','80;echo','-1']:
            with self.subTest(port=port):
                result=self.run_installer(port)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual((self.install/'.env').read_text(),original)

if __name__=='__main__':
    unittest.main()
