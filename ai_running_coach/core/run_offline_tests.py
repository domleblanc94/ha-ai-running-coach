"""Run the public suite with outbound socket connections blocked."""
from pathlib import Path
import socket
import os
import unittest
from unittest.mock import patch

def denied(*args,**kwargs):raise RuntimeError("Network is disabled in offline tests")

class IsolatedEnvironmentResult(unittest.TextTestResult):
    def startTest(self,test):
        self.saved_environment=dict(os.environ)
        super().startTest(test)
    def stopTest(self,test):
        os.environ.clear();os.environ.update(self.saved_environment)
        super().stopTest(test)

if __name__=="__main__":
    with patch.object(socket.socket,"connect",denied),patch.object(socket.socket,"connect_ex",denied),patch.object(socket,"create_connection",denied):
        # Source connector imports must not load a developer's real .env during discovery.
        with patch("dotenv.load_dotenv",return_value=False):
            suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent/"tests"))
        result=unittest.TextTestRunner(verbosity=2,resultclass=IsolatedEnvironmentResult).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
