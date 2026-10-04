package dev.vidtheque.app

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onRoot
import com.github.takahirom.roborazzi.captureRoboImage
import androidx.compose.material3.SnackbarHostState
import dev.vidtheque.app.data.Hits
import dev.vidtheque.app.data.History
import dev.vidtheque.app.data.Misses
import dev.vidtheque.app.data.Profile
import dev.vidtheque.app.data.ProfileEntry
import dev.vidtheque.app.data.Regret
import dev.vidtheque.app.data.SearchHit
import dev.vidtheque.app.data.ValuedTime
import dev.vidtheque.app.data.Week
import dev.vidtheque.app.ui.profile.ProfileContent
import dev.vidtheque.app.ui.profile.ProfileUi
import dev.vidtheque.app.ui.search.SearchContent
import dev.vidtheque.app.ui.search.SearchUi
import dev.vidtheque.app.ui.signin.SignInScreen
import dev.vidtheque.app.ui.theme.VidthequeTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

// JVM renders for PR bodies: this box has no KVM, so no emulator.
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [36], qualifiers = "w411dp-h891dp-xxhdpi")
class ScreenshotTest {
    @get:Rule val compose = createComposeRule()

    @Test
    fun signIn() {
        compose.setContent {
            // Dark, as the reference was recorded; the theme follows the system since #130.
            VidthequeTheme(darkTheme = true) {
                Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
                    SignInScreen(host = "private.vidtheque.dev", error = null, busy = false, onSignIn = {})
                }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/sign-in.png")
    }

    @Test
    fun search() {
        val hits = listOf(
            SearchHit("kCc8FmEb1nY", "Let's build GPT: from scratch, in code, spelled out.", "Andrej Karpathy", "transcript", 840.0, 842.5, "so the kv cache is just the keys and values of every token you have seen so far", "https://youtu.be/kCc8FmEb1nY?t=840", 1674000000),
            SearchHit("kCc8FmEb1nY", "Let's build GPT: from scratch, in code, spelled out.", "Andrej Karpathy", "ocr", 5.0, 5.0, "kv cache size = 2 * n_layers * n_heads", "https://youtu.be/kCc8FmEb1nY?t=3", 1674000000),
            SearchHit("zduSFxRajkE", "Let's build the GPT Tokenizer", "Andrej Karpathy", "frame", 1210.0, 1210.0, null, "https://youtu.be/zduSFxRajkE?t=1208", 1708000000),
        )
        compose.setContent {
            VidthequeTheme(darkTheme = true) {
                SearchContent(SearchUi(query = "kv cache", hits = hits))
            }
        }
        compose.onRoot().captureRoboImage("screenshots/search.png")
    }

    @Test
    fun valuedTime() {
        val ledger = ValuedTime(
            0.1,
            listOf(
                Week(1791756000, true, Hits(3, 7, 0.429), Regret(1, 6, 0.167), Misses(2, 1, 4)),
                Week(1791151200, false, Hits(4, 10, 0.4), Regret(0, 5, 0.0), Misses(1, 0, 2)),
            ),
        )
        val profile = Profile(
            12,
            40,
            listOf(ProfileEntry(7, "Coding agent evals", 0.9, "nightly"), ProfileEntry(3, "Local inference", 0.6, "owner")),
            History(emptyList(), false),
        )
        compose.setContent {
            VidthequeTheme(darkTheme = true) {
                ProfileContent(
                    ui = ProfileUi(profile = profile),
                    snackbar = SnackbarHostState(),
                    onBack = {}, onSignOut = {}, onRetry = {}, onDrop = {}, onRevert = {}, onOlder = {}, onBuild = {},
                    ledger = ledger,
                )
            }
        }
        compose.onRoot().captureRoboImage("screenshots/valued-time.png")
    }
}
