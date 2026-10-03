package dev.vidtheque.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import dev.vidtheque.app.ui.theme.Archivo

/** The logo is the word, with the receipt's full stop in gold (DESIGN.md, The logo). */
@Composable
fun Wordmark(modifier: Modifier = Modifier) {
    Text(
        buildAnnotatedString {
            append("vidtheque")
            withStyle(SpanStyle(color = MaterialTheme.colorScheme.primary)) { append(".") }
        },
        style = TextStyle(fontFamily = Archivo, fontSize = 20.sp, letterSpacing = (-0.035).em),
        color = MaterialTheme.colorScheme.onSurface,
        modifier = modifier,
    )
}
