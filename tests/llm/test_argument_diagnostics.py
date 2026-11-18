"""Fixed enum diagnostics: preserve admission failure without leaking model values."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from jsonschema import Draft202012Validator
from agent.llm.interface import AdapterError, ToolCall, ToolDefinition
from agent.llm.ollama import _call, OllamaConfig
from agent.llm.async_adapter import _completion, AsyncLLMError

SCHEMA={'type':'object','properties':{'port':{'type':'integer','const':80}},
        'required':['port'],'additionalProperties':False}
TOOL=ToolDefinition('listening_ports','Trusted read-only profile',SCHEMA)
VALIDATORS={TOOL.name:Draft202012Validator(SCHEMA)}


class ArgumentDiagnosticsTests(unittest.TestCase):
    def test_invalid_arguments_keep_code_and_safe_category(self):
        cases=[(ToolCall('not-allowed',{}),'UNKNOWN_TOOL'),
               (ToolCall(TOOL.name,None),'ARGUMENTS_NOT_OBJECT'),
               (ToolCall(TOOL.name,{}),'REQUIRED_ARGUMENT'),
               (ToolCall(TOOL.name,{'port':80,'private-canary':'synthetic-value'}),'UNEXPECTED_ARGUMENT'),
               (ToolCall(TOOL.name,{'port':'80'}),'ARGUMENT_TYPE'),
               (ToolCall(TOOL.name,{'port':22}),'ARGUMENT_VALUE')]
        for call,reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(AdapterError) as error:_call(call,VALIDATORS)
                self.assertEqual(error.exception.code,'INVALID_ARGUMENTS')
                self.assertEqual(error.exception.reason,reason)
                self.assertEqual(str(error.exception),'INVALID_ARGUMENTS')
                with self.assertRaises(AsyncLLMError) as parent:
                    _completion(json.dumps(dict(ok=False,code=error.exception.code,
                        reason=error.exception.reason)).encode(),(TOOL,),OllamaConfig())
                self.assertEqual(parent.exception.reason,reason)
    def test_no_untrusted_ipc_diagnostics(self):
        for packet in [dict(ok=False,code='INVALID_ARGUMENTS',reason='synthetic-secret'),
                       dict(ok=False,code='INVALID_ARGUMENTS',reason='TOOL_REQUIRED'),
                       dict(ok=False,code='OUTPUT_POLICY',reason='UNKNOWN_TOOL'),
                       dict(ok=False,code='INVALID_ARGUMENTS',reason='ARGUMENT_VALUE',value='secret')]:
            with self.assertRaises(AsyncLLMError) as error:_completion(json.dumps(packet).encode(),(),OllamaConfig())
            self.assertEqual(error.exception.code,'MALFORMED_RESPONSE')
    def test_worker_ipc_emits_enum_only_no_invalid_value(self):
        script='''
import json
from agent.llm._async_worker import serve
class HTTP:
 def post(self,url,body,timeout,limit):
  request=json.loads(body)
  return json.dumps(dict(model=request['model'],done=True,done_reason='stop',
   message=dict(role='assistant',content='',tool_calls=[dict(function=dict(name='listening_ports',
    arguments={'port':80,'private-canary':'synthetic-value'}))]))).encode()
raise SystemExit(serve(HTTP()))
'''
        from dataclasses import asdict
        request=dict(config=asdict(OllamaConfig()),messages=[dict(role='user',content='Synthetic',tool_calls=[],tool_name=None)],
                     tools=[asdict(TOOL)],require_tool=True)
        result=subprocess.run([sys.executable,'-B','-c',script],input=json.dumps(request).encode(),capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stderr,b'')
        self.assertEqual(json.loads(result.stdout),dict(ok=False,code='INVALID_ARGUMENTS',reason='UNEXPECTED_ARGUMENT'))
        self.assertNotIn(b'canary',result.stdout);self.assertNotIn(b'synthetic-value',result.stdout)
    def test_valid_arguments_unchanged(self):
        self.assertIsNone(_call(ToolCall(TOOL.name,{'port':80}),VALIDATORS))
