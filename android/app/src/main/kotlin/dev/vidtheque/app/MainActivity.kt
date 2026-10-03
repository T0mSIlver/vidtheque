package dev.vidtheque.app

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.browser.auth.AuthTabIntent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dagger.hilt.android.AndroidEntryPoint
import dev.vidtheque.app.auth.SessionState
import dev.vidtheque.app.ui.RootViewModel
import dev.vidtheque.app.ui.SignedInScreen
import dev.vidtheque.app.ui.signin.SignInScreen
import dev.vidtheque.app.ui.signin.SignInViewModel
import dev.vidtheque.app.ui.theme.VidthequeTheme

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    private val root: RootViewModel by viewModels()
    private val signIn: SignInViewModel by viewModels()

    // Registered before the activity starts, as the Activity Result API requires.
    private val authTab = AuthTabIntent.registerActivityResultLauncher(this) { signIn.onAuthTabResult(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        val splash = installSplashScreen()
        splash.setKeepOnScreenCondition { root.state.value == SessionState.Loading }
        // The app is dark only, so the bars' icons stay light whatever the system mode.
        enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) handleCallback(intent)
        setContent {
            VidthequeTheme {
                val state by root.state.collectAsStateWithLifecycle()
                val ui by signIn.ui.collectAsStateWithLifecycle()
                Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
                    when (state) {
                        SessionState.Loading -> Unit
                        SessionState.SignedIn -> SignedInScreen(signIn.host, onSignOut = root::signOut)
                        SessionState.SignedOut -> SignInScreen(
                            host = signIn.host,
                            error = ui.error,
                            busy = ui.busy,
                            onSignIn = {
                                signIn.start { url, host, path -> AuthTabIntent.Builder().build().launch(authTab, url, host, path) }
                            },
                        )
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleCallback(intent)
    }

    // A browser without Auth Tab returns through the verified App Link instead.
    private fun handleCallback(intent: Intent) {
        val uri = intent.data ?: return
        if (intent.action == Intent.ACTION_VIEW && uri.path == "/auth/android/callback") signIn.onRedirect(uri)
    }
}
