"""Lossless format tolerance; malformed grading data must never become invented scores."""
import json
import unittest
from unittest.mock import patch
import httpx
from app.llm import call_model, parse_json, ModelError, TruncatedOutputError
from app.schemas import Grade
from test_app import grade


class JsonFormatTests(unittest.TestCase):
    def test_formatting_repairs_preserve_exact_answer_and_scores(self):
        expected=dict(grade(17.25),high_score_answer='标题\n　　第一段包含“引号”、路径 C:\\paper\\answer 和字面量 \\n。\n　　第二段。')
        raw=json.dumps(expected,ensure_ascii=False)
        variants=[raw,'\ufeff```JSON\n'+raw+'\n```','以下是批改结果：\n'+raw+'\n批改结束。',raw[:-1]+',}',raw.replace('标题\\n','标题\n'),'<think>不作为最终答案的内容</think>\n'+raw]
        for text in variants:
            with self.subTest(text=text[:30]):
                actual=parse_json(text)
                self.assertEqual(actual,expected)
                self.assertEqual(Grade.model_validate(actual).score,17.25)

    def test_never_guess_missing_content_or_choose_between_answers(self):
        for raw in ['[]','not json','{"score":17','{"score":17} {"score":19}','说明：{"score":17}\n{"score":19}']:
            with self.subTest(raw=raw),self.assertRaises(ModelError):parse_json(raw)
        # Formatting repair does not fill missing dimensions/evidence or clamp bad values.
        for raw in [{'score':17},dict(grade(),score=-1),dict(grade(),dimensions={'审题立意':101})]:
            with self.assertRaises(ValueError):Grade.model_validate(parse_json(json.dumps(raw)))


class TransportFormatTests(unittest.IsolatedAsyncioTestCase):
    async def request(self,body,protocol='openai',sse=False):
        original=httpx.AsyncClient
        self.calls=0
        def handle(request):
            self.calls+=1
            if protocol=='gemini':self.assertIn('/models/mock-model:generateContent',str(request.url))
            else:self.assertEqual(json.loads(request.content)['model'],'mock-model')
            return httpx.Response(200,text=body,headers={'content-type':'text/event-stream'}) if sse else httpx.Response(200,json=body)
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
            return await call_model(dict(protocol=protocol,base_url='https://mock.example/v1',model='mock-model',api_key='secret-do-not-expose',max_tokens=6000),'批改',{})

    async def test_openai_gateway_final_body_shapes(self):
        expected=grade(18);raw=json.dumps(expected,ensure_ascii=False)
        bodies=[
            {'choices':[{'message':{'content':raw}}]},
            {'choices':[{'message':{'content':[{'type':'text','text':raw[:25]},{'type':'text','text':raw[25:]}]}}]},
            {'choices':[{'message':{'content':{'type':'text','text':raw}}}]},
            {'choices':[{'message':{'parsed':expected,'content':None}}]},
            {'choices':[{'message':{'content':expected}}]},
            {'data':{'choices':[{'message':{'content':'说明：\n'+raw}}]}},
            {'output':[{'type':'reasoning','content':[]},{'type':'message','content':[{'type':'output_text','text':raw}]}]},
            {'output_text':raw},expected,
        ]
        for body in bodies:
            with self.subTest(shape=list(body)):
                self.assertEqual(await self.request(body),expected)
                self.assertEqual(self.calls,1)  # No extra billed request for cosmetic repairs.

    async def test_other_protocols_ignore_thinking_and_keep_final_text(self):
        expected=grade();raw=json.dumps(expected)
        self.assertEqual(await self.request({'content':[{'type':'thinking','thinking':'private'},{'type':'text','text':raw}],'stop_reason':'end_turn'},'anthropic'),expected)
        self.assertEqual(await self.request({'candidates':[{'content':{'parts':[{'thought':True,'text':'private'},{'text':raw}]},'finishReason':'STOP'}]},'gemini'),expected)

    async def test_sse_final_text_and_usage_for_all_protocols(self):
        expected=grade();raw=json.dumps(expected)
        streams={
            'openai':[{'choices':[{'delta':{'role':'assistant'}}]},{'choices':[{'delta':{'reasoning_content':'private'}}]},{'choices':[{'delta':{'content':raw[:20]}}]},{'choices':[{'delta':{'content':raw[20:]},'finish_reason':'stop'}],'usage':{'completion_tokens':123}}],
            'anthropic':[{'type':'content_block_start','content_block':{'type':'text','text':raw[:20]}},{'type':'content_block_delta','delta':{'type':'text_delta','text':raw[20:]}},{'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':123}},{'type':'message_stop'}],
            'gemini':[{'candidates':[{'content':{'parts':[{'text':raw}]}}]},{'candidates':[{'finishReason':'STOP'}],'usageMetadata':{'candidatesTokenCount':123}}],
        }
        for protocol,events in streams.items():
            stream='\ufeff: heartbeat\r\n\r\n'+'\r\n\r\n'.join('data: '+json.dumps(e) for e in events)+'\r\n\r\ndata: [DONE]\r\n\r\n'
            with self.subTest(protocol=protocol):
                actual=await self.request(stream,protocol,sse=True)
                self.assertEqual(actual,expected);self.assertEqual(actual.usage['output_tokens'],123)

    async def test_incomplete_stream_and_reported_truncation_never_count_as_success(self):
        raw=json.dumps(grade())
        events=[{'choices':[{'delta':{'content':raw}}]}]
        with self.assertRaises(ModelError) as caught:await self.request('data: '+json.dumps(events[0])+'\n\n',sse=True)
        self.assertIn('中断',str(caught.exception))
        with self.assertRaises(TruncatedOutputError):
            await self.request('data: '+json.dumps({'choices':[{'delta':{'content':raw},'finish_reason':'length'}]})+'\n\n',sse=True)
        with self.assertRaises(TruncatedOutputError):await self.request({'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},'output_text':raw})

    async def test_mismatched_envelopes_and_thinking_do_not_expose_private_content(self):
        for body in [[],{'unexpected':'secret-do-not-expose'},{'choices':[{'message':{'reasoning_content':'private secret-do-not-expose'}}]},{'choices':[{'message':{'content':'<think>private</think>'}}]}]:
            with self.subTest(body=body),self.assertRaises(ModelError) as caught:await self.request(body)
            self.assertNotIn('secret-do-not-expose',str(caught.exception));self.assertNotIn('private',str(caught.exception))
