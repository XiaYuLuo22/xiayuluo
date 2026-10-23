package com.xiayuluo.assistant.voice

import org.junit.Assert.*
import org.junit.Test

class SpeechTextTest {
    @Test fun longRepliesAreBoundedAndPreserveAllNonWhitespaceText() {
        val text = "夏鱼洛的普通回复。".repeat(1300)
        val chunks = SpeechText.chunks(text)
        assertTrue(chunks.size > 1)
        assertTrue(chunks.all { it.length <= 3000 })
        assertEquals(text, chunks.joinToString(""))
    }
    @Test fun sentenceBoundaryPreferredButNoEmptyChunks() {
        assertEquals(listOf("abcdef。", "ghijklmn"), SpeechText.chunks("abcdef。ghijklmn", 10))
        assertEquals(emptyList<String>(), SpeechText.chunks("  \n "))
    }
    @Test fun surrogatePairsAreNotSplit() {
        val text = "夏😀😀😀😀😀😀😀😀"
        val chunks = SpeechText.chunks(text, 4)
        assertEquals(text, chunks.joinToString(""))
        assertTrue(chunks.all { !Character.isLowSurrogate(it.first()) && !Character.isHighSurrogate(it.last()) })
    }
}
