package com.xiayuluo.assistant.voice

/** Pure offline transcript gate. Never forward partial hypotheses or pre-wake text. */
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
