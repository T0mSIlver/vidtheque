package dev.vidtheque.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import dev.vidtheque.app.ui.SignInScreen
import dev.vidtheque.app.ui.theme.VidthequeTheme
import dev.vidtheque.app.ui.theme.Vt

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            VidthequeTheme {
                Box(Modifier.fillMaxSize().background(Vt.pitch)) {
                    SignInScreen(
                        host = BuildConfig.INSTANCE.substringAfter("://"),
                        error = null,
                        busy = false,
                        onSignIn = {},
                    )
                }
            }
        }
    }
}
