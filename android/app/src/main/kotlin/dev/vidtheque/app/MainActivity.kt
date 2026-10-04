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
import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.auth.SessionState
import dev.vidtheque.app.data.WatchClock
import dev.vidtheque.app.push.VerdictNotifications
import dev.vidtheque.app.ui.RootViewModel
import dev.vidtheque.app.ui.SignedIn
import dev.vidtheque.app.ui.signin.SignInScreen
import dev.vidtheque.app.ui.signin.SignInViewModel
import dev.vidtheque.app.ui.theme.VidthequeTheme
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    private val root: RootViewModel by viewModels()
    private val signIn: SignInViewModel by viewModels()

    @Inject lateinit var watchClock: WatchClock

    // Registered before the activity starts, as the Activity Result API requires.
    private val authTab = AuthTabIntent.registerActivityResultLauncher(this) { signIn.onAuthTabResult(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        val splash = installSplashScreen()
        splash.setKeepOnScreenCondition { root.state.value == SessionState.Loading }
        // The bars' icons follow the system's light or dark mode, as the theme does.
        enableEdgeToEdge(SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT), SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) handleCallback(intent)
        setContent {
            VidthequeTheme {
                val state by root.state.collectAsStateWithLifecycle()
                val ui by signIn.ui.collectAsStateWithLifecycle()
                val typed by signIn.typed.collectAsStateWithLifecycle()
                Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
                    when (state) {
                        SessionState.Loading -> Unit
                        SessionState.SignedIn -> SignedIn(opening = root.opening, openingBrief = root.openingBrief, onSignOut = root::signOut)
                        SessionState.SignedOut -> SignInScreen(
                            instance = typed ?: signIn.current,
                            onInstance = signIn::type,
                            error = ui.error,
                            busy = ui.busy,
                            onSignIn = {
                                signIn.start { url -> AuthTabIntent.Builder().build().launch(authTab, url, Instance.REDIRECT_SCHEME) }
                            },
                        )
                    }
                }
            }
        }
    }

    // Back from YouTube after a moment was handed to it: that time is the watch's length.
    override fun onStart() {
        super.onStart()
        watchClock.returned()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleCallback(intent)
    }

    // A browser without Auth Tab returns through the intent filter instead.
    private fun handleCallback(intent: Intent) {
        intent.getStringExtra(VerdictNotifications.EXTRA_VIDEO)?.let { root.opening.value = it }
        if (intent.getBooleanExtra(VerdictNotifications.EXTRA_BRIEF, false)) root.openingBrief.value = true
        val uri = intent.data ?: return
        if (intent.action == Intent.ACTION_VIEW && uri.scheme == Instance.REDIRECT_SCHEME && uri.path == Instance.REDIRECT_PATH) {
            signIn.onRedirect(uri)
        }
    }
}
