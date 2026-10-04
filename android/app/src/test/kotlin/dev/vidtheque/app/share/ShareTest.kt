package dev.vidtheque.app.share

import dev.vidtheque.app.data.Shared
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ShareTest {
    @Test
    fun theFirstLinkInTheSharedTextIsTheOneSent() {
        assertEquals("https://youtu.be/kCc8FmEb1nY?si=x", sharedLink("https://youtu.be/kCc8FmEb1nY?si=x"))
        assertEquals("https://youtu.be/kCc8FmEb1nY", sharedLink("Let's build GPT https://youtu.be/kCc8FmEb1nY via YouTube"))
        assertNull(sharedLink("no link here"))
        assertNull(sharedLink(null))
    }

    @Test
    fun theToastSaysWhetherTheFeedMissedIt() {
        assertEquals("Indexing it. A miss: scored 1.", sharedLine(Shared("v", true, "scored 1")))
        assertEquals("The feed had it: scored 3.", sharedLine(Shared("v", false, "scored 3")))
        assertEquals("Indexing it. Whether the feed missed it is known once it is judged.", sharedLine(Shared("v", null, "no verdict yet")))
    }
}
