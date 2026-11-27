"""Run the public suite with outbound socket connections blocked."""
from pathlib import Path
import socket
import os
import unittest
from unittest.mock import patch

def denied(*args,**kwargs):raise RuntimeError("Network is disabled in offline tests")

# Windows implements socketpair using loopback connect. Keep asyncio's local
# wake-up pipe working without reopening arbitrary connections in the suite.
_socket_connect = socket.socket.connect
_native_socketpair = socket.socketpair

def local_socketpair(family=None, type=socket.SOCK_STREAM, proto=0):
    if os.name != 'nt':
        return _native_socketpair() if family is None else _native_socketpair(family,type,proto)
    family = socket.AF_INET if family is None else family
    if family not in (socket.AF_INET,socket.AF_INET6) or type != socket.SOCK_STREAM:
        raise ValueError('Offline socket pairs support loopback TCP only')
    host = '127.0.0.1' if family == socket.AF_INET else '::1'
    client = None
    try:
        with socket.socket(family,type,proto) as listener:
            listener.bind((host,0));listener.listen(1)
            client = socket.socket(family,type,proto)
            _socket_connect(client,listener.getsockname())
            server,_ = listener.accept()
        return server,client
    except BaseException:
        if client is not None:client.close()
        raise


class IsolatedEnvironmentResult(unittest.TextTestResult):
    def startTest(self,test):
        self.saved_environment=dict(os.environ)
        super().startTest(test)
    def stopTest(self,test):
        os.environ.clear();os.environ.update(self.saved_environment)
        super().stopTest(test)

if __name__=="__main__":
    with patch.object(socket,"socketpair",local_socketpair),patch.object(socket.socket,"connect",denied),patch.object(socket.socket,"connect_ex",denied),patch.object(socket,"create_connection",denied):
        # Source connector imports must not load a developer's real .env during discovery.
        with patch("dotenv.load_dotenv",return_value=False):
            suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent/"tests"))
        result=unittest.TextTestRunner(verbosity=2,resultclass=IsolatedEnvironmentResult).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
