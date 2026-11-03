import unittest
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from app.schemas import ProviderInput
from app.llm import call_model, TruncatedOutputError

class LimitsTests(unittest.IsolatedAsyncioTestCase):
    def test_default_and_supported_config_range(self):
        config=dict(name='test',base_url='https://mock.example/v1',model='test')
        self.assertEqual(ProviderInput(**config).max_tokens,32768)
        self.assertEqual(ProviderInput(**config,max_tokens=131072).max_tokens,131072)
        with self.assertRaises(ValidationError):ProviderInput(**config,max_tokens=131073)
        from app.model_limits import next_token_limit
        self.assertEqual(next_token_limit(32000),65536)
        self.assertIsNone(next_token_limit(131072))
    async def test_all_protocols_receive_large_limit_and_report_truncation(self):
        original=httpx.AsyncClient
        for protocol in ['openai','anthropic','gemini']:
            def respond(request):
                import json
                data=json.loads(request.content)
                self.assertEqual(data.get('max_completion_tokens') or data.get('max_tokens') or data.get('generationConfig',{}).get('maxOutputTokens'),65536)
                result={'openai':{'choices':[{'finish_reason':'length','message':{'content':'partial'}}],'usage':{'completion_tokens':65536,'completion_tokens_details':{'reasoning_tokens':60000}}},
                        'anthropic':{'stop_reason':'max_tokens','usage':{'output_tokens':65536}},
                        'gemini':{'candidates':[{'finishReason':'MAX_TOKENS'}],'usageMetadata':{'candidatesTokenCount':10,'thoughtsTokenCount':60000}}}[protocol]
                return httpx.Response(200,json=result)
            with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
                with self.assertRaises(TruncatedOutputError) as error:
                    await call_model(dict(protocol=protocol,base_url='https://mock.example/v1',model='test',max_tokens=65536),'test',{})
                self.assertEqual(error.exception.token_limit,65536)
                self.assertEqual(error.exception.suggested_token_limit,131072)
                self.assertIn('65536',str(error.exception))
    async def test_usage_is_metadata_not_model_authored_fields(self):
        original=httpx.AsyncClient
        def respond(request):return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true,"usage":"invented"}'}}],'usage':{'completion_tokens':120,'completion_tokens_details':{'reasoning_tokens':30}}})
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(respond),**kw)):
            result=await call_model(dict(protocol='openai',base_url='https://mock.example/v1',model='test',max_tokens=32768),'test',{})
        self.assertEqual(result.usage,{'output_tokens':120,'reasoning_tokens':30})
