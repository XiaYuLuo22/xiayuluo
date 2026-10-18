package com.xiayuluo.assistant.voice

import org.json.JSONObject
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

class VoiceConnection(val url: String, val token: String)

/** Uses only paired mobile endpoints, default TLS checks and no redirects. Never retries POST. */
class VoiceChat(private val config: VoiceConnection, private val stopped: () -> Boolean) {
    private val connections = java.util.concurrent.ConcurrentHashMap.newKeySet<HttpURLConnection>()
    @Volatile private var voiceToken: String? = null
    var session: String? = null; private set
    fun cancel() { connections.forEach { it.disconnect() }; connections.clear() }
    private fun close(conn: HttpURLConnection) { connections.remove(conn); conn.disconnect() }
    private fun open(path: String, body: JSONObject? = null, revoking: Boolean = false): HttpURLConnection {
        check(revoking || !stopped())
        val conn = URL(config.url + "/mobile/v1/" + path).openConnection() as HttpURLConnection
        conn.instanceFollowRedirects = false
        conn.connectTimeout = if (revoking) 3000 else 15_000
        conn.readTimeout = if (revoking) 3000 else if (path.startsWith("voice/")) 10_000 else 100_000
        conn.setRequestProperty("Authorization", "Bearer " + config.token)
        conn.setRequestProperty("Content-Type", "application/json")
        connections.add(conn)
        try {
            if (body != null) { conn.requestMethod = "POST"; conn.doOutput = true; conn.outputStream.use { it.write(body.toString().toByteArray(StandardCharsets.UTF_8)) } }
            check(conn.responseCode == 200) { "配对/网络/聊天请求失败，不会自动重发。" }
            return conn
        } catch (error: Exception) { close(conn); throw error }
    }
    private fun request(path: String, body: JSONObject, revoking: Boolean = false): JSONObject {
        val conn = open(path, body, revoking)
        return try { conn.inputStream.use { JSONObject(bounded(it, 8192)) } } finally { close(conn) }
    }
    fun begin() {
        val lease = request("voice/sessions", JSONObject().put("confirm", true))
        val id = lease.getString("session_id")
        check(Regex("[a-f0-9]{32}").matches(id)); session = id
        voiceToken = lease.getString("voice_token")
    }
    fun renew() { request("voice/heartbeat", JSONObject().put("voice_token", voiceToken ?: error("No lease"))) }
    fun revoke() {
        val token = voiceToken ?: return
        try { request("voice/revoke", JSONObject().put("voice_token", token), true) } finally { voiceToken = null }
    }
    private fun bounded(input: InputStream, limit: Int): String {
        val bytes = java.io.ByteArrayOutputStream()
        val data = ByteArray(4096)
        while (true) { val count = input.read(data); if (count < 0) break
            check(bytes.size() + count <= limit); bytes.write(data, 0, count) }
        return bytes.toString("UTF-8")
    }
    fun ask(prompt: String): String {
        var active: HttpURLConnection? = null
        try {
            check(session != null && voiceToken != null)
            val conn = open("chat", JSONObject().put("session_id", session).put("message", prompt)
                .put("mode", "chat").put("voice_token", voiceToken))
            active = conn
            val reply = StringBuilder(); var done = false; var pending = false
            conn.inputStream.bufferedReader(StandardCharsets.UTF_8).use { reader ->
                val line = StringBuilder()
                while (!stopped()) {
                    val char = reader.read(); if (char == -1) break
                    if (char == 10) {
                        val value = line.toString().trimEnd('\r'); line.setLength(0)
                        if (!value.startsWith("data:")) continue
                        val event = JSONObject(value.substring(5).trim())
                        when (event.optString("type")) {
                            "error" -> error("本轮回复失败，不会自动重发。")
                            "text" -> { reply.append(event.optString("text")); check(reply.length <= 100_000) }
                            "action" -> pending = true
                            "done" -> done = true
                        }
                    } else { check(line.length < 65_536); line.append(char.toChar()) }
                }
            }
            check(done && !stopped()) { "连接中断，本轮未完成。" }
            return if (pending) "本轮需要你在电脑待确认任务中批准。批准后的回答请查看聊天记录。" else reply.toString().take(1800).ifBlank { "本轮没有可朗读的回复。" }
        } finally { active?.let { close(it) } }
    }
}
