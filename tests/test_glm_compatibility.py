import json
import unittest
from unittest.mock import patch
import httpx
from app.llm import call_model,ModelError

class GLMCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self,handle,base='https://open.bigmodel.cn/api/paas/v4'):
        original=httpx.AsyncClient
        provider=dict(protocol='openai',base_url=base,model='glm-5.3',max_tokens=131072,api_key='secret-api-key')
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
            return await call_model(provider,'独立批改',{'answer':'私密作答原文'})

    async def test_official_glm_uses_max_tokens_without_lowering_budget(self):
        def handle(request):
            body=json.loads(request.content)
            self.assertEqual(body['max_tokens'],131072)
            self.assertNotIn('max_completion_tokens',body)
            self.assertNotIn('thinking',body)
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
        self.assertTrue((await self.ask(handle))['ok'])

    async def test_structured_unsupported_parameter_falls_back_once(self):
        calls=[]
        def handle(request):
            body=json.loads(request.content);calls.append(body)
            if len(calls)==1:return httpx.Response(400,json={'error':{'code':'unsupported_parameter','param':'max_tokens'}})
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
        self.assertTrue((await self.ask(handle))['ok'])
        self.assertEqual(len(calls),2);self.assertEqual(calls[1]['max_completion_tokens'],131072)
        self.assertNotIn('max_tokens',calls[1])

    async def test_range_error_is_not_retried_or_disclosed(self):
        calls=[]
        def handle(request):
            calls.append(1)
            return httpx.Response(400,json={'error':{'code':'1210','param':'max_tokens','message':'max_tokens exceeds allowed range; secret-api-key 私密作答原文'}})
        with self.assertRaises(ModelError) as caught:await self.ask(handle)
        self.assertEqual(len(calls),1)
        text=str(caught.exception)
        self.assertIn('1210',text);self.assertIn('max_tokens=131072',text);self.assertIn('输出',text)
        self.assertNotIn('secret-api-key',text);self.assertNotIn('私密作答',text)

    async def test_secret_reflection_and_unknown_codes_are_never_echoed(self):
        for body in [{'error':{'code':'secret-api-key','param':'私密作答原文','message':'secret-api-key 私密作答原文'}},None]:
            def handle(request):return httpx.Response(400,json=body) if body else httpx.Response(400,text='secret-api-key 私密作答原文')
            with self.assertRaises(ModelError) as caught:await self.ask(handle)
            self.assertNotIn('secret-api-key',str(caught.exception));self.assertNotIn('私密作答',str(caught.exception))

    async def test_other_openai_compatible_services_keep_original_parameter(self):
        def handle(request):
            self.assertEqual(json.loads(request.content)['max_completion_tokens'],131072)
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
        await self.ask(handle,'https://other.example/v1')
