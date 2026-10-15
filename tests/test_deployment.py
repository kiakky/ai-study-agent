import importlib.util
import json
import ssl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deployment import Deployment, load_deployment, validate_public_url

spec = importlib.util.spec_from_file_location('launch_script', ROOT / 'scripts' / 'run.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class DeploymentTests(unittest.TestCase):
    def test_public_url_validation(self):
        self.assertEqual(validate_public_url('https://study.example/'), 'https://study.example')
        for value in ['http://study.example', 'https://localhost', 'https://127.0.0.1',
                      'https://user:secret@study.example', 'https://study.example/?token=x',
                      'https://study.example/#x', 'https://study.example/path',
                      'https://study.example:8000', 'https://x.local', 'https://x.internal',
                      'https://x.localhost', 'https://study.example\n', 'https://[::1]', None, 123]:
            with self.subTest(value=value):
                self.assertRaises(ValueError, validate_public_url, value)

    def test_config_is_non_secret_and_optional(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests', prefix='quiz-test-') as folder:
            path = Path(folder) / 'deployment.json'
            self.assertEqual(load_deployment(path), Deployment())
            path.write_text(json.dumps({'mini_app_url': 'https://study.example/', 'ngrok_executable': 'C:/Tools/ngrok.exe'}))
            config = load_deployment(path)
            self.assertEqual(config.allowed_hosts, ['127.0.0.1', 'localhost', '[::1]', 'study.example'])
            path.write_text('{"authtoken": "synthetic"}')
            self.assertRaises(ValueError, load_deployment, path)
            path.write_text('not JSON')
            self.assertRaises(ValueError, load_deployment, path)

    def test_launch_commands_are_loopback_only_and_keep_inspection_off(self):
        config = Deployment('https://study.example', 'C:/Tools/ngrok.exe')
        api = launcher.command_for('api', config)
        self.assertEqual(api[api.index('--host') + 1], '127.0.0.1')
        self.assertIn('--no-access-log', api)
        self.assertNotIn('--reload', api)
        tunnel = launcher.command_for('tunnel', config)
        self.assertEqual(tunnel, ['C:/Tools/ngrok.exe', 'http', 'http://127.0.0.1:8000', '--url', 'https://study.example', '--inspect=false'])
        self.assertRaises(ValueError, launcher.command_for, 'tunnel', Deployment())
        self.assertRaises(ValueError, launcher.command_for, 'bot', Deployment())

    def test_probe_does_not_accept_interstitial_as_app(self):
        from io import BytesIO
        response = BytesIO(b'<html>Visit Site - provider warning</html>')
        response.status = 200
        response.headers = {'Content-Type': 'text/html'}
        with patch.object(launcher, 'urlopen', side_effect=[response, OSError('synthetic')]), patch('builtins.print'):
            self.assertFalse(launcher.probe('https://study.example'))

    def test_probe_uses_certifi_and_rejects_tls_validation_failure(self):
        failure = URLError(ssl.SSLCertVerificationError('Synthetic certificate validation failure'))
        with patch.object(launcher.ssl, 'create_default_context', wraps=ssl.create_default_context) as create_context:
            with patch.object(launcher, 'urlopen', side_effect=failure) as request, patch('builtins.print'):
                self.assertFalse(launcher.probe('https://study.example'))
        create_context.assert_called_once_with(cafile=launcher.certifi.where())
        request.assert_called_once()
        context = request.call_args.kwargs['context']
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


if __name__ == '__main__':
    unittest.main()
