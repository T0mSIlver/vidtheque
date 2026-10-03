package dev.vidtheque.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.unit.dp
import dev.vidtheque.app.ui.theme.Vt
import dev.vidtheque.app.ui.theme.VtType

@Composable
fun SignInScreen(
    host: String,
    error: String?,
    busy: Boolean,
    onSignIn: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier.fillMaxSize().safeDrawingPadding().padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Wordmark()
        Spacer(Modifier.weight(1f))
        Text("What should I watch?", style = VtType.display, color = Vt.fg)
        Text(
            "Verdicts on every new video from the channels you follow, and the moments worth your time.",
            style = VtType.body,
            color = Vt.fg2,
        )
        Spacer(Modifier.height(12.dp))
        Text(host, style = VtType.machine, color = Vt.fg2)
        if (error != null) Text(error, style = VtType.cell, color = Vt.toneBad)
        // Material buttons default to a pill; DESIGN.md has no radius.
        Button(onClick = onSignIn, enabled = !busy, shape = RectangleShape, modifier = Modifier.fillMaxWidth().height(52.dp)) {
            Text(if (busy) "Signing in" else "Sign in", style = VtType.action)
        }
    }
}
