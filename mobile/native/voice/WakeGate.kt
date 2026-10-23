package com.xiayuluo.assistant.voice

/** Legacy wake-word gate, retained for compatibility, not used by VoiceService. */
class WakeGate(private val clock: () -> Long = { System.nanoTime() / 1_000_000 }) {
    private var until = 0L
    fun resume() { until = clock() + 30_000 }
    fun feed(input: String): Pair<String, String> {
        var text = input.replace(Regex("\\s+"), "").take(2000)
        val name = listOf("夏鱼洛", "夏雨洛", "夏雨落", "下雨落").firstOrNull { text.contains(it) }
        if (name != null) { text = text.substringAfter(name).trim('，', '。', '！', '？', ',', '.', '!', '?', '、'); resume() }
        else if (clock() >= until) return "ignore" to ""
        if (text.trim('，', '。', '！', '？', ',', '.', '!', '?', '、') in setOf("停止对话", "结束对话", "退出语音", "停止监听")) {
            until = 0L
            return (if (text.trim('，', '。', '！', '？', ',', '.', '!', '?', '、') in setOf("退出语音", "停止监听")) "stop" else "sleep") to ""
        }
        if (text.isEmpty()) return (if (name != null) "wake" else "ignore") to ""
        resume(); return "chat" to text
    }
}

/** No-wake gate for a visibly enabled microphone. Only completed offline ASR is fed.
 * Nearby speech is not speaker-authenticated. Stop always closes this gate; it
 * never returns to a hidden waiting-for-wake state or rearms itself later.
 */
class ConversationGate {
    private var stopped = false
    fun feed(input: String): Pair<String, String> {
        if (stopped) return "ignore" to ""
        val text = input.replace(Regex("\\s+"), " ").trim().take(2000)
            .trim('，', '。', '！', '？', ',', '.', '!', '?', '、', ' ')
        if (text.isEmpty()) return "ignore" to ""
        var command = text.replace(Regex("\\s+"), "")
        val name = listOf("夏鱼洛", "夏雨洛", "夏雨落", "下雨落").firstOrNull { command.startsWith(it) }
        if (name != null) command = command.removePrefix(name).trim('，', '。', '！', '？', ',', '.', '!', '?', '、')
        if (command in setOf("停止对话", "结束对话", "退出语音", "停止监听")) {
            stopped = true
            return "stop" to ""
        }
        return "chat" to text
    }
}
