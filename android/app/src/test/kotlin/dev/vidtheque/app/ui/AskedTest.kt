package dev.vidtheque.app.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class AskedTest {
    @Test
    fun theMomentsMinutesAgainstTheVideos() {
        assertEquals("6\u00A0of\u00A042\u00A0min", asked(372.0, 2520.0))
        // A short moment costs a minute, never more than the video.
        assertEquals("1\u00A0of\u00A042\u00A0min", asked(10.0, 2520.0))
        assertEquals("10\u00A0of\u00A010\u00A0min", asked(900.0, 600.0))
    }

    @Test
    fun theLengthWithNoSpanOrNoMoment() {
        assertEquals("42:00", asked(null, 2520.0))
        assertEquals("42:00", asked(0.0, 2520.0))
    }
}
