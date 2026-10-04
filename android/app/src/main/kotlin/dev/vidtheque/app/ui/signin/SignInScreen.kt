package dev.vidtheque.app.ui.signin

import dev.vidtheque.app.ui.Wordmark

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun SignInScreen(
    instance: String,
    onInstance: (String) -> Unit,
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
        OutlinedTextField(
            value = instance,
            onValueChange = onInstance,
            label = { Text("Your vidtheque instance") },
            placeholder = { Text("vidtheque.example.com") },
            supportingText = { Text(error ?: "The address of the vidtheque server you run.") },
            isError = error != null,
            singleLine = true,
            enabled = !busy,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
            keyboardActions = KeyboardActions(onGo = { onSignIn() }),
            shape = MaterialTheme.shapes.large,
            modifier = Modifier.fillMaxWidth(),
        )
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
