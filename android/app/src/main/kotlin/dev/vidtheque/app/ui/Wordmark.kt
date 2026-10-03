package dev.vidtheque.app.ui

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import dev.vidtheque.app.ui.theme.Vt
import dev.vidtheque.app.ui.theme.VtType

/** The logo is the word, with the receipt's full stop in gold (DESIGN.md, The logo). */
@Composable
fun Wordmark(modifier: Modifier = Modifier) {
    Text(
        buildAnnotatedString {
            append("vidtheque")
            withStyle(SpanStyle(color = Vt.gold)) { append(".") }
        },
        style = VtType.wordmark,
        color = Vt.fg,
        modifier = modifier,
    )
}
