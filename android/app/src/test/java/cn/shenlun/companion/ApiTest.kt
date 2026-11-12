package cn.shenlun.companion
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID

class ApiTest {
    @Test fun imageBodiesUseEachProtocolWithoutKeysOrQuestionMaterials() {
        listOf("openai","anthropic","gemini").forEach { protocol ->
            val body=OcrApi.body(OcrConfig(protocol,"https://api.example/v1","vision-model","secret-key"),listOf("JPEG_PAGE_1","JPEG_PAGE_2"),"max_tokens").toString()
            assertTrue(body.contains("JPEG_PAGE_1"));assertTrue(body.contains("JPEG_PAGE_2"));assertFalse(body.contains("secret-key"));assertFalse(body.contains("question_signature"));assertTrue(body.contains("不批改、不润色、不补写"))
        }
    }
    @Test fun transcriptionKeepsIndentationAndOptionalUncertainty() {
        val text="　　原答用词不改。\n　　这里有□。"
        assertEquals(text,OcrApi.parse(JSONObject().put("text",text).toString()).text);assertEquals(text,OcrApi.parse(text).text)
        assertEquals(listOf("第1页第二段"),OcrApi.parse("```json\n"+JSONObject().put("text",text).put("uncertain",JSONArray().put("第1页第二段"))+"\n```").uncertain)
        try { OcrApi.parse("{\"text\":\"\"}");fail() } catch(e:ApiFailure) { assertTrue(e.message!!.contains("未返回有效文字")) }
    }
    @Test fun outputParameterFallbackPreservesImagesAndBudget() = runBlocking {
        val server=MockWebServer();server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(400).setBody("{\"error\":{\"param\":\"max_completion_tokens\",\"code\":\"unsupported_parameter\"}}"))
            server.enqueue(MockResponse().setBody(JSONObject().put("choices",JSONArray().put(JSONObject().put("finish_reason","stop").put("message",JSONObject().put("content","{\"text\":\"　　手写答案。\"}")))).toString()))
            assertEquals("　　手写答案。",OcrApi.recognize(OcrConfig(url=server.url("/v1").toString(),model="vision",key="secret-test",maxTokens=8192),listOf("page-image")).text)
            val first=server.takeRequest();val second=server.takeRequest();val a=JSONObject(first.body.readUtf8());val b=JSONObject(second.body.readUtf8())
            assertEquals(8192,a.getInt("max_completion_tokens"));assertEquals(8192,b.getInt("max_tokens"));assertEquals(a.getJSONArray("messages").toString(),b.getJSONArray("messages").toString());assertEquals("Bearer secret-test",first.getHeader("Authorization"));assertEquals(2,server.requestCount)
        } finally { server.shutdown() }
    }
    @Test fun upstreamErrorDoesNotReflectKeyOrImageContent() = runBlocking {
        val server=MockWebServer();server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(400).setBody("{\"error\":{\"message\":\"secret-test IMAGE_PRIVATE reflected\"}}"))
            try { OcrApi.recognize(OcrConfig(url=server.url("/v1").toString(),model="vision",key="secret-test"),listOf("IMAGE_PRIVATE"));fail() }
            catch(e:ApiFailure) { assertFalse(e.message!!.contains("secret-test"));assertFalse(e.message!!.contains("IMAGE_PRIVATE"));assertTrue(e.message!!.contains("HTTP 400")) };assertEquals(1,server.requestCount)
        } finally { server.shutdown() }
    }
    @Test fun syncSendsOnlyTextToSelectedQuestionAndReusesRequestId() = runBlocking {
        val server=MockWebServer();server.start()
        try {
            server.enqueue(MockResponse().setBody("{\"id\":\"capture-id\"}"));val api=ServerApi(ServerConfig(server.url("").toString().trimEnd('/'),"paired-device-token","phone"))
            val q=Question("question-3",3,"essay","题干","a".repeat(64));val draft=AnswerDraft(text="　　校对后的原文。",requestId=UUID.randomUUID().toString())
            assertEquals("capture-id",api.send(Paper("paper-1","试卷","2026","浙江","A卷",3),q,draft,"vision"))
            val request=server.takeRequest();assertEquals("/api/mobile/papers/paper-1/questions/question-3/captures",request.path)
            val json=JSONObject(request.body.readUtf8());assertEquals(draft.requestId,json.getString("request_id"));assertEquals(draft.text,json.getString("text"));assertFalse(json.has("photos"));assertFalse(json.has("api_key"))
        } finally { server.shutdown() }
    }
    @Test fun anthropicAndGeminiResponsesUseMatchingHeadersAndExcludeThinking() = runBlocking {
        for(protocol in listOf("anthropic","gemini")) {
            val server=MockWebServer();server.start()
            try {
                val text=JSONObject().put("text","　　视觉模型转录原文。").toString()
                val response=if(protocol=="anthropic")JSONObject().put("content",JSONArray().put(JSONObject().put("type","text").put("text",text))).put("usage",JSONObject().put("output_tokens",30))
                    else JSONObject().put("candidates",JSONArray().put(JSONObject().put("finishReason","STOP").put("content",JSONObject().put("parts",JSONArray().put(JSONObject().put("thought",true).put("text","hidden thinking")).put(JSONObject().put("text",text)))))).put("usageMetadata",JSONObject().put("candidatesTokenCount",30))
                server.enqueue(MockResponse().setBody(response.toString()))
                val result=OcrApi.recognize(OcrConfig(protocol,server.url("/v1").toString(),"vision","provider-key"),listOf("image"))
                assertEquals("　　视觉模型转录原文。",result.text);assertEquals(30,result.outputTokens)
                val request=server.takeRequest();assertEquals("provider-key",request.getHeader(if(protocol=="anthropic")"x-api-key" else "x-goog-api-key"));assertNull(request.getHeader("Authorization"))
            } finally { server.shutdown() }
        }
    }
    @Test fun publicHttpAndCredentialUrlsAreRejected() {
        listOf("http://public.example/v1","https://user:key@example.com/v1","https://example.com/v1?key=secret").forEach { try { Network.base(it);fail(it) } catch(_:ApiFailure) { } }
        assertEquals("http://192.168.1.3:18086",Network.base("http://192.168.1.3:18086/"))
    }
}
