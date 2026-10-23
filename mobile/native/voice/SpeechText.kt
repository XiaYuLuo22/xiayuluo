package com.xiayuluo.assistant.voice

/** Bounded local-TTS chunks, favouring sentence boundaries without splitting UTF-16 pairs. */
object SpeechText {
    fun chunks(text: String, limit: Int = 3000): List<String> {
        require(limit >= 2)
        val result = mutableListOf<String>()
        var start = 0
        while (start < text.length) {
            var end = minOf(start + limit, text.length)
            if (end < text.length) {
                val boundary = text.substring(start, end).indexOfLast { it in "。！？；\n.!?;" }
                if (boundary >= limit / 2) end = start + boundary + 1
                if (Character.isHighSurrogate(text[end - 1]) && Character.isLowSurrogate(text[end])) end--
            }
            text.substring(start, end).trim().takeIf { it.isNotEmpty() }?.let(result::add)
            start = end
        }
        return result
    }
}
