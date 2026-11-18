"""No Windows subprocess is run here; platform-native verification remains separate."""
from types import SimpleNamespace
import unittest
from agent.mcp_client.sdk import _child_environment
from agent.mcp_client import MCPClientError


class ChildEnvironmentTests(unittest.TestCase):
    def test_windows_systemroot_restored_without_credentials_or_parent_path(self):
        env=_child_environment(SimpleNamespace(env={}),['SYSTEMROOT','USERPROFILE','PATH'],platform='nt',
            parent={'SystemRoot':r'C:\Windows','USERPROFILE':r'C:\Users\private',
                    'PATH':'untrusted-parent','API_KEY':'synthetic'})
        self.assertEqual(env['SYSTEMROOT'],r'C:\Windows')
        self.assertEqual(env['PATH'],r'C:\Windows\System32')
        self.assertEqual(env['USERPROFILE'],'')
        self.assertNotIn('API_KEY',env)
    def test_windows_missing_or_relative_root_fails_closed(self):
        for value in [None,'','relative','bad\x00value']:
            with self.subTest(value=value):
                with self.assertRaises(MCPClientError):
                    _child_environment(SimpleNamespace(env={}),['SYSTEMROOT'],platform='nt',
                        parent={} if value is None else {'SYSTEMROOT':value})
    def test_posix_environment_remains_minimal(self):
        env=_child_environment(SimpleNamespace(env={'LANG':'C.UTF-8'}),['HOME','PATH','USER'],
            platform='posix',parent={'HOME':'private','SYSTEMROOT':'ignored'})
        self.assertEqual(env,{'HOME':'','USER':'','PATH':'/usr/bin:/bin',
                            'PYTHONUNBUFFERED':'1','LANG':'C.UTF-8'})
    def test_parent_environment_is_not_modified(self):
        parent={'SYSTEMROOT':r'C:\Windows','PATH':'parent'};original=dict(parent)
        _child_environment(SimpleNamespace(env={}),['SYSTEMROOT'],platform='nt',parent=parent)
        self.assertEqual(parent,original)


if __name__=='__main__':unittest.main()
