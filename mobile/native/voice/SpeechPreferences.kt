package com.xiayuluo.assistant.voice

/** Local playback preferences only; Android speed is relative, not exact WPM. */
data class SpeechPreferences(val voiceId: String?, val rate: Int, val volume: Float) {
    val speed: Float get() = rate / 180f

    companion object {
        const val DEFAULT_RATE = 180
        const val DEFAULT_VOLUME = 1f

        fun validated(voiceId: String?, rate: Double, volume: Double): SpeechPreferences {
            require(voiceId == null || (voiceId.isNotBlank() && voiceId.length <= 1024 &&
                voiceId.none { it.code < 32 || it.code == 127 })) { "音色编号无效。" }
            require(rate.isFinite() && rate % 1.0 == 0.0 && rate in 80.0..300.0) { "语速必须为80至300之间的整数。" }
            require(volume.isFinite() && volume in 0.0..1.0) { "音量必须为0至1之间的数值。" }
            return SpeechPreferences(voiceId, rate.toInt(), volume.toFloat())
        }
    }
}
