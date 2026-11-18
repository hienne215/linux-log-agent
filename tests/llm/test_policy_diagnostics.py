"""Policy diagnostics are enums, never raw provider content or reasoning."""
import json
import unittest
from agent.llm.interface import AdapterError
from agent.llm.ollama import OllamaAdapter,OllamaConfig
from agent.llm.async_adapter import _completion,AsyncLLMError


class PolicyDiagnosticsTests(unittest.TestCase):
    def test_safe_reasons_distinguish_policy_failures(self):
        adapter=OllamaAdapter()
        base=dict(model=OllamaConfig().model,done=True,done_reason='stop',message={'role':'assistant','content':'text'})
        for change,require,reason in [({},True,'TOOL_REQUIRED'),({'done_reason':'length'},True,'INCOMPLETE_GENERATION'),
                ({'message':{'role':'assistant','content':'','tool_calls':[{},{}]}},True,'MULTIPLE_TOOL_CALLS'),
                ({'message':{'role':'assistant','content':'','thinking':'synthetic-private-canary'}},True,'REASONING_PRESENT'),
                ({'message':{'role':'assistant','content':''}},False,'EMPTY_RESPONSE'),
                ({'eval_count':257},False,'TOKEN_LIMIT')]:
            with self.subTest(reason=reason):
                with self.assertRaises(AdapterError) as e:adapter._normalize({**base,**change},{},require,0,False)
                self.assertEqual(e.exception.code,'OUTPUT_POLICY')
                self.assertEqual(e.exception.reason,reason)
                self.assertNotIn('synthetic-private-canary',str(e.exception))
    def test_valid_enum_crosses_ipc_without_changing_primary_code(self):
        raw=json.dumps({'ok':False,'code':'OUTPUT_POLICY','reason':'MULTIPLE_TOOL_CALLS'}).encode()
        with self.assertRaises(AsyncLLMError) as e:_completion(raw,(),OllamaConfig())
        self.assertEqual(e.exception.code,'OUTPUT_POLICY')
        self.assertEqual(e.exception.reason,'MULTIPLE_TOOL_CALLS')
    def test_untrusted_diagnostic_text_rejected_at_parent_boundary(self):
        for packet in [{'ok':False,'code':'OUTPUT_POLICY','reason':'not-enum-synthetic'},
                       {'ok':False,'code':'OUTPUT_POLICY','reason':'TOOL_REQUIRED','raw':'synthetic-private'},
                       {'ok':False,'code':'TIMEOUT','reason':'TOOL_REQUIRED'}]:
            with self.assertRaises(AsyncLLMError) as e:_completion(json.dumps(packet).encode(),(),OllamaConfig())
            self.assertEqual(e.exception.code,'MALFORMED_RESPONSE')
            self.assertNotIn('synthetic-private',str(e.exception))


if __name__=='__main__':unittest.main()
