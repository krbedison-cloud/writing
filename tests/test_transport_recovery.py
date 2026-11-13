import asyncio
import json
import socket
import ssl
import unittest
from unittest.mock import patch
import httpx
from app.llm import call_model, ModelError


class TransportRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def request(self,handler,seconds=600):
        original=httpx.AsyncClient
        settings=dict(protocol='openai',base_url='https://mock.example/v1',model='deepseek-v4-pro',api_key='secret-do-not-echo',max_tokens=32768,timeout_seconds=seconds)
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            return await call_model(settings,'独立批改',{})

    async def test_connection_not_established_can_reconnect_once(self):
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)==1:raise httpx.ConnectError('secret-do-not-echo',request=request)
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
        result=await self.request(handler)
        self.assertTrue(result['ok']);self.assertEqual(len(calls),2)
        self.assertEqual(result.connection_retries,1)
        self.assertEqual(calls[0].content,calls[1].content)

    async def test_reconnection_is_bounded_and_deadline_is_not_reset(self):
        calls=[]
        def handler(request):calls.append(request);raise httpx.ConnectError('secret-do-not-echo',request=request)
        with self.assertRaises(ModelError) as caught:await self.request(handler)
        self.assertEqual(len(calls),2);self.assertEqual(caught.exception.code,'connect_failed')
        self.assertEqual(caught.exception.connection_retries,1)
        self.assertNotIn('secret-do-not-echo',str(caught.exception))
        calls.clear()
        with self.assertRaises(ModelError) as caught:await self.request(handler,seconds=.02)
        self.assertEqual(len(calls),1);self.assertIn('0.02',str(caught.exception))

    async def test_submitted_requests_are_not_retried_after_disconnect(self):
        for error in [httpx.ConnectError,httpx.RemoteProtocolError,httpx.ReadError,httpx.WriteError,httpx.ReadTimeout,httpx.DecodingError]:
            calls=[]
            async def handler(request):
                calls.append(request)
                await request.extensions['trace']('http11.send_request_headers.started',{})
                await request.extensions['trace']('http11.receive_response_headers.started',{})
                raise error('secret-do-not-echo 私密答案',request=request)
            with self.subTest(error=error.__name__),self.assertRaises(ModelError) as caught:await self.request(handler)
            self.assertEqual(len(calls),1)
            self.assertNotIn('secret-do-not-echo',str(caught.exception));self.assertNotIn('私密答案',str(caught.exception))
            self.assertEqual(caught.exception.connection_retries,0)
            self.assertTrue(caught.exception.code)

    async def test_phase_trace_uses_only_event_names_and_preserves_failure_stage(self):
        async def handler(request):
            await request.extensions['trace']('http11.receive_response_headers.complete',{'headers':'secret-do-not-echo'})
            raise httpx.RemoteProtocolError('secret-do-not-echo')
        with self.assertRaises(ModelError) as caught:await self.request(handler)
        self.assertEqual(caught.exception.phase,'reading_response')
        self.assertEqual(caught.exception.code,'response_disconnected')
        self.assertIn('读取响应',str(caught.exception))
        self.assertNotIn('secret-do-not-echo',str(caught.exception))

    async def test_certificate_and_permanent_dns_failures_are_not_retried(self):
        for cause,code in [(ssl.SSLCertVerificationError('secret-do-not-echo'),'tls_certificate'),(socket.gaierror(socket.EAI_NONAME,'secret-do-not-echo'),'dns_failure')]:
            calls=[]
            def handler(request):
                calls.append(request)
                raise httpx.ConnectError('secret-do-not-echo',request=request) from cause
            with self.subTest(code=code),self.assertRaises(ModelError) as caught:await self.request(handler)
            self.assertEqual(len(calls),1);self.assertEqual(caught.exception.code,code)
            self.assertNotIn('secret-do-not-echo',str(caught.exception))

    async def test_external_cancellation_propagates(self):
        ready=asyncio.Event()
        async def handler(request):ready.set();await asyncio.sleep(5)
        task=asyncio.create_task(self.request(handler))
        await ready.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
