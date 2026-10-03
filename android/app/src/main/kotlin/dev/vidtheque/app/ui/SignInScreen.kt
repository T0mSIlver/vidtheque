package dev.vidtheque.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun SignInScreen(
    host: String,
    error: String?,
    busy: Boolean,
    onSignIn: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val type = MaterialTheme.typography
    val colors = MaterialTheme.colorScheme
    Column(
        modifier.fillMaxSize().safeDrawingPadding().padding(horizontal = 24.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Wordmark()
        Spacer(Modifier.weight(1f))
        Text("What should I watch?", style = type.displayMediumEmphasized, color = colors.onSurface)
        Text(
            "Verdicts on every new video from the channels you follow, and the moments worth your time.",
            style = type.bodyLarge,
            color = colors.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        Surface(color = colors.surfaceContainerHigh, shape = MaterialTheme.shapes.large) {
            Text(host, style = type.labelLarge, color = colors.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp))
        }
        if (error != null) Text(error, style = type.bodyMedium, color = colors.error)
        Button(
            onClick = onSignIn,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth().height(ButtonDefaults.LargeContainerHeight),
            contentPadding = ButtonDefaults.contentPaddingFor(ButtonDefaults.LargeContainerHeight),
        ) {
            if (busy) LoadingIndicator(Modifier.size(32.dp), color = colors.onPrimary)
            else Text("Sign in", style = ButtonDefaults.textStyleFor(ButtonDefaults.LargeContainerHeight))
        }
    }
}
