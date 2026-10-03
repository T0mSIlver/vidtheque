package dev.vidtheque.app

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onRoot
import com.github.takahirom.roborazzi.captureRoboImage
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
            VidthequeTheme {
                Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
                    SignInScreen(host = "private.vidtheque.dev", error = null, busy = false, onSignIn = {})
                }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/sign-in.png")
    }
}
