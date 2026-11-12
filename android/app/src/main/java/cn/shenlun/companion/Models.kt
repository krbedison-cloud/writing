package cn.shenlun.companion

import org.json.JSONArray
import org.json.JSONObject

data class ServerConfig(val url: String = "", val token: String = "", val deviceId: String = "", val serverId: String = "")
data class OcrConfig(val protocol: String = "openai", val url: String = "https://api.openai.com/v1", val model: String = "", val key: String = "", val maxTokens: Int = 8192, val timeout: Int = 600)
data class Paper(val id: String, val title: String, val year: String, val province: String, val volume: String, val questionCount: Int)
data class Question(val id: String, val number: Int, val kind: String, val prompt: String, val signature: String, val wordMax: Int = 0)
data class PhotoPage(val path: String, val rotation: Int = 0)
data class AnswerDraft(val text: String = "", val photos: List<PhotoPage> = emptyList(), val uncertain: List<String> = emptyList(), val requestId: String = "", val syncedText: String? = null)
data class SavedData(val server: ServerConfig = ServerConfig(), val ocr: OcrConfig = OcrConfig(), val drafts: Map<String, AnswerDraft> = emptyMap(), val lastPaper: Paper? = null, val lastQuestion: Question? = null)
data class OcrResult(val text: String, val uncertain: List<String>, val outputTokens: Int? = null)

fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }
fun JSONArray.strings(): List<String> = (0 until length()).mapNotNull { optString(it).takeIf(String::isNotBlank) }
fun Paper.toJson() = JSONObject().put("id", id).put("title", title).put("year", year).put("province", province).put("volume", volume).put("question_count", questionCount)
fun paperFrom(j: JSONObject) = Paper(j.getString("id"), j.getString("title"), j.optString("year"), j.optString("province"), j.optString("volume"), j.optInt("question_count"))
fun Question.toJson() = JSONObject().put("id", id).put("number", number).put("kind", kind).put("prompt", prompt).put("question_signature", signature).put("word_max", wordMax)
fun questionFrom(j: JSONObject) = Question(j.getString("id"), j.getInt("number"), j.getString("kind"), j.getString("prompt"), j.getString("question_signature"), j.optInt("word_max"))
fun kindLabel(kind: String) = when(kind) { "summary" -> "概括 / 短简报 · 20分"; "practical" -> "应用文 / 短评 · 30分"; else -> "大作文 · 50分" }
