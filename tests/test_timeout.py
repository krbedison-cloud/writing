"""Model timeout configuration, compatibility and cancellation without real APIs."""
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.db import Database
from app.llm import call_model, ModelError
from app.schemas import ProviderInput


def provider(**values):
    return dict(protocol='openai', base_url='https://mock.example/v1', api_key='test-secret', model='slow', max_tokens=6000, **values)


class TimeoutSettingsTests(unittest.TestCase):
    def test_default_bounds_and_old_config_persistence(self):
        base=dict(name='slow', protocol='openai', base_url='https://mock.example/v1', model='slow', api_key='test-secret')
        self.assertEqual(ProviderInput(**base).timeout_seconds,600)
        for seconds in (60,1800):
            self.assertEqual(ProviderInput(**base,timeout_seconds=seconds).timeout_seconds,seconds)
        for seconds in (0,59,1801):
            with self.assertRaises(ValidationError):ProviderInput(**base,timeout_seconds=seconds)
        with tempfile.TemporaryDirectory() as temp:
            db=Database(temp)
            old=dict(base,max_tokens=6000)
            db.save_provider('old',old)
            config_before=db.rows('SELECT config,secret FROM providers WHERE id=?',('old',))[0]
            self.assertNotIn('timeout_seconds',json.loads(config_before['config']))
            self.assertEqual(db.provider('old')['timeout_seconds'],600)
            self.assertEqual(db.provider('old',private=True)['api_key'],'test-secret')
            self.assertEqual(dict(db.rows('SELECT config,secret FROM providers WHERE id=?',('old',))[0]),dict(config_before))
            update=ProviderInput(**base,timeout_seconds=1200).model_dump()
            update['api_key']=''
            db.save_provider('old',update)
            reopened=Database(temp).provider('old',private=True)
            self.assertEqual(reopened['timeout_seconds'],1200)
            self.assertEqual(reopened['api_key'],'test-secret')
            self.assertEqual(reopened['model'],'slow')


class TimeoutTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_protocol_receives_configured_budget_and_old_configs_default(self):
        original=httpx.AsyncClient
        for protocol in ('openai','anthropic','gemini'):
            for seconds in (None,1200):
                with self.subTest(protocol=protocol,seconds=seconds):
                    def handle(request):
                        configured=600 if seconds is None else seconds
                        self.assertEqual(request.extensions['timeout']['read'],configured)
                        self.assertEqual(request.extensions['timeout']['connect'],20)
                        body={'openai':{'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]},
                              'anthropic':{'content':[{'type':'text','text':'{"ok":true}'}]},
                              'gemini':{'candidates':[{'content':{'parts':[{'text':'{"ok":true}'}]}}]}}[protocol]
                        return httpx.Response(200,json=body)
                    settings=provider();settings['protocol']=protocol
                    if seconds is not None:settings['timeout_seconds']=seconds
                    with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
                        self.assertTrue((await call_model(settings,'test',{}))['ok'])

    async def test_slow_response_times_out_once_and_preserves_secret(self):
        original=httpx.AsyncClient;calls=0
        async def handle(request):
            nonlocal calls
            calls+=1
            await asyncio.sleep(1)
            return httpx.Response(200,json={})
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
            with self.assertRaises(ModelError) as error:
                await call_model(provider(timeout_seconds=.02),'test',{})
        self.assertEqual(calls,1)
        self.assertIn('0.02',str(error.exception))
        self.assertIn('模型设置',str(error.exception))
        self.assertIn('重试该教练',str(error.exception))
        self.assertNotIn('test-secret',str(error.exception))

    async def test_compatibility_fallback_shares_one_deadline(self):
        original=httpx.AsyncClient;calls=0
        async def handle(request):
            nonlocal calls
            calls+=1
            await asyncio.sleep(.13)
            if calls==1:return httpx.Response(400,text='unsupported max_completion_tokens')
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
            with self.assertRaises(ModelError):await call_model(provider(timeout_seconds=.2),'test',{})
        self.assertEqual(calls,2)

    async def test_transport_timeout_and_external_cancel_are_distinct(self):
        original=httpx.AsyncClient
        async def timed_out(request):raise httpx.ReadTimeout('test-secret')
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(timed_out),**kw)):
            with self.assertRaises(ModelError) as error:await call_model(provider(timeout_seconds=900),'test',{})
        self.assertIn('900',str(error.exception));self.assertNotIn('test-secret',str(error.exception))
        started=asyncio.Event()
        async def blocked(request):
            started.set();await asyncio.sleep(10)
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(blocked),**kw)):
            task=asyncio.create_task(call_model(provider(timeout_seconds=600),'test',{}))
            await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task


if __name__=='__main__':unittest.main()
