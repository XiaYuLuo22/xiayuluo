package com.xiayuluo.assistant.voice

import org.junit.Assert.*
import org.junit.Test

class SpeechPreferencesTest {
    @Test fun boundedSettingsUseRelativeAndroidSpeed() {
        val defaults = SpeechPreferences.validated(null, 180.0, 1.0)
        assertEquals(1f, defaults.speed)
        assertEquals(.5f, SpeechPreferences.validated("local-zh", 90.0, .5).speed)
        assertEquals(0f, SpeechPreferences.validated("local-zh", 80.0, 0.0).volume)
        assertEquals(300, SpeechPreferences.validated("local-zh", 300.0, 1.0).rate)
    }

    @Test fun malformedSettingsAreRejectedInsteadOfClamped() {
        for (rate in listOf(79.0, 301.0, 180.5, Double.NaN, Double.POSITIVE_INFINITY)) {
            try { SpeechPreferences.validated(null, rate, 1.0); fail("invalid rate") }
            catch (_: IllegalArgumentException) {}
        }
        for (volume in listOf(-.1, 1.1, Double.NaN, Double.NEGATIVE_INFINITY)) {
            try { SpeechPreferences.validated(null, 180.0, volume); fail("invalid volume") }
            catch (_: IllegalArgumentException) {}
        }
        for (voice in listOf("", " ", "bad\nvoice", "x".repeat(1025))) {
            try { SpeechPreferences.validated(voice, 180.0, 1.0); fail("invalid voice") }
            catch (_: IllegalArgumentException) {}
        }
    }
}
