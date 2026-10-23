package com.xiayuluo.assistant.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class WakeGateTest {
    @Test fun ambientIsNeverForwarded() {
        var now = 100_000L
        val gate = WakeGate { now }
        assertEquals("ignore" to "", gate.feed("这是环境私人对话"))
        assertEquals("chat" to "今天星期几", gate.feed("前文不要发送 夏 鱼 洛，今天星期几"))
        assertEquals("chat" to "继续", gate.feed("继续"))
        now += 31_000
        assertEquals("ignore" to "", gate.feed("超时后不发送"))
    }
    @Test fun stopListeningClosesMicrophoneNotJustConversation() {
        val gate = WakeGate { 100_000L }
        assertEquals("wake" to "", gate.feed("夏雨洛"))
        assertEquals("sleep" to "", gate.feed("结束对话"))
        assertEquals("ignore" to "", gate.feed("环境对话"))
        assertEquals("stop" to "", gate.feed("夏鱼洛停止监听"))
    }
    @Test fun timeoutStartsAfterAnswerAndInputIsBounded() {
        var now = 100_000L
        val gate = WakeGate { now }
        gate.feed("夏鱼洛"); now += 60_000; gate.resume()
        assertEquals("chat" to "继续", gate.feed("继续"))
        assertEquals(2000, gate.feed("x".repeat(9000)).second.length)
    }

    @Test fun conversationNeedsNoNameAndPreservesEnglishWords() {
        val gate = ConversationGate()
        assertEquals("chat" to "今天星期几", gate.feed("今天星期几"))
        assertEquals("chat" to "hello world", gate.feed("  hello   world  "))
        assertEquals("chat" to "夏鱼洛，请介绍一下你自己", gate.feed("夏鱼洛，请介绍一下你自己"))
        assertEquals("ignore" to "", gate.feed(" \n ，。！？,.!?、 "))
        assertEquals(2000, gate.feed("x".repeat(9000)).second.length)
    }

    @Test fun endingConversationClosesGateAndCannotBeRearmedByName() {
        listOf("停止对话", "结束对话", "退出语音", "停止监听", "夏鱼洛，停止监听！").forEach { command ->
            val gate = ConversationGate()
            assertEquals("stop" to "", gate.feed(command))
            assertEquals("ignore" to "", gate.feed("夏鱼洛"))
            assertEquals("ignore" to "", gate.feed("稍后这句也不发送"))
        }
    }
}
