package cn.shenlun.companion

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecureStore(context: Context) {
    private val file = AtomicFile(File(context.filesDir, "companion.enc"))
    private val alias = "shenlun-companion-private-v1"
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
            generateKey()
        }
    }
    @Synchronized fun read(): SavedData {
        if (!file.baseFile.exists()) return SavedData()
        val bytes = file.readFully()
        require(bytes.size > 28) { "本地配置不完整，原文件已保留" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0,12)))
        return decode(JSONObject(String(cipher.doFinal(bytes.copyOfRange(12,bytes.size)), Charsets.UTF_8)))
    }
    @Synchronized fun save(data: SavedData) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val encrypted = cipher.iv + cipher.doFinal(encode(data).toString().toByteArray(Charsets.UTF_8))
        val out = file.startWrite()
        try { out.write(encrypted); file.finishWrite(out) } catch (e: Exception) { file.failWrite(out); throw e }
    }
    private fun encode(data: SavedData): JSONObject {
        val drafts = JSONObject()
        data.drafts.forEach { (id,d) -> drafts.put(id,JSONObject().put("text",d.text).put("photos",JSONArray(d.photos.map { JSONObject().put("path",it.path).put("rotation",it.rotation) }))
            .put("uncertain",JSONArray(d.uncertain)).put("request_id",d.requestId).put("synced_text",d.syncedText ?: JSONObject.NULL)) }
        return JSONObject().put("server",JSONObject().put("url",data.server.url).put("token",data.server.token).put("device_id",data.server.deviceId).put("server_id",data.server.serverId))
            .put("ocr",JSONObject().put("protocol",data.ocr.protocol).put("url",data.ocr.url).put("model",data.ocr.model).put("key",data.ocr.key).put("max_tokens",data.ocr.maxTokens).put("timeout",data.ocr.timeout))
            .put("drafts",drafts).put("last_paper",data.lastPaper?.toJson()).put("last_question",data.lastQuestion?.toJson())
    }
    private fun decode(j: JSONObject): SavedData {
        val s = j.optJSONObject("server") ?: JSONObject(); val o = j.optJSONObject("ocr") ?: JSONObject()
        val d = j.optJSONObject("drafts") ?: JSONObject()
        val drafts = d.keys().asSequence().associateWith { id -> val x=d.getJSONObject(id); AnswerDraft(x.optString("text"),(x.optJSONArray("photos") ?: JSONArray()).objects().map { PhotoPage(it.getString("path"),it.optInt("rotation")) },(x.optJSONArray("uncertain") ?: JSONArray()).strings(),x.optString("request_id"),if(x.isNull("synced_text")) null else x.optString("synced_text")) }
        return SavedData(ServerConfig(s.optString("url"),s.optString("token"),s.optString("device_id"),s.optString("server_id")),OcrConfig(o.optString("protocol","openai"),o.optString("url","https://api.openai.com/v1"),o.optString("model"),o.optString("key"),o.optInt("max_tokens",8192),o.optInt("timeout",600)),drafts,j.optJSONObject("last_paper")?.let(::paperFrom),j.optJSONObject("last_question")?.let(::questionFrom))
    }
}
