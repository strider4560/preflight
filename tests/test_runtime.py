"""Exercise the real local Testinfra transport and TLS verification, without AWS."""

import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from checks.runtime.test_http import test_bootstrap_endpoint as check_endpoint
from gate.contract import Probe


@pytest.fixture
def tls_endpoint(tmp_path, monkeypatch):
    if not shutil.which("curl") or not shutil.which("openssl"):
        pytest.skip("local TLS self-test needs curl and openssl")
    certificate, key = tmp_path / "certificate.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ready")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / ".curlrc").write_text("insecure\n")
    monkeypatch.setenv("CURL_HOME", str(tmp_path))
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    for name in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    try:
        yield f"https://127.0.0.1:{server.server_port}/ready", certificate
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_runtime_does_not_accept_insecure_user_curl_configuration(tls_endpoint):
    url, _ = tls_endpoint
    probe = Probe(name="tls", host="local://", url=url)
    with pytest.raises(pytest.fail.Exception, match="exit 60"):
        check_endpoint(probe)


def test_runtime_accepts_explicitly_trusted_endpoint(tls_endpoint, monkeypatch):
    url, certificate = tls_endpoint
    monkeypatch.setenv("CURL_CA_BUNDLE", str(certificate))
    check_endpoint(Probe(name="tls", host="local://", url=url))
