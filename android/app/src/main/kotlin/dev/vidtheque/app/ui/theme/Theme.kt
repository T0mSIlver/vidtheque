package dev.vidtheque.app.ui.theme

import androidx.compose.foundation.shape.ZeroCornerSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/** DESIGN.md's tokens, read from web/src/styles/tokens.css. Dark only, one accent. */
object Vt {
    val pitch = Color(0xFF040405)
    val void = Color(0xFF08080A)
    val console = Color(0xFF0A0A0E)
    val plate = Color(0xFF0E0E12)
    val plate2 = Color(0xFF141419)
    val plate3 = Color(0xFF1A1A20)
    val seam = Color(0xFF242429)
    val seam2 = Color(0xFF33333B)
    val fg = Color(0xFFF3F0EA)
    val fg2 = Color(0xFF9D968C)
    val fgOver = Color(0xFFC9C2B6)
    val gold = Color(0xFFE7B455)
    val goldHi = Color(0xFFF6CD78)
    val goldInk = Color(0xFF120C02)
    val gold12 = Color(0x1FE7B455)
    val toneOk = Color(0xFF71D083)
    val toneWarn = Color(0xFFFFA057)
    val toneBad = Color(0xFFFF9592)
    val toneWork = Color(0xFF70B8FF)
}

// Material components read these; anything Material has no slot for uses Vt directly.
private val scheme = darkColorScheme(
    primary = Vt.gold,
    onPrimary = Vt.goldInk,
    primaryContainer = Vt.gold12,
    onPrimaryContainer = Vt.gold,
    secondary = Vt.fg2,
    onSecondary = Vt.pitch,
    background = Vt.pitch,
    onBackground = Vt.fg,
    surface = Vt.pitch,
    onSurface = Vt.fg,
    surfaceVariant = Vt.plate,
    onSurfaceVariant = Vt.fg2,
    surfaceContainerLowest = Vt.pitch,
    surfaceContainerLow = Vt.void,
    surfaceContainer = Vt.plate,
    surfaceContainerHigh = Vt.plate2,
    surfaceContainerHighest = Vt.plate3,
    outline = Vt.seam2,
    outlineVariant = Vt.seam,
    error = Vt.toneBad,
    onError = Vt.pitch,
    scrim = Color(0xA8000000),
)

// Zero radius everywhere: a frame has square corners (DESIGN.md, Layout).
private val square = Shapes(
    extraSmall = androidx.compose.foundation.shape.RoundedCornerShape(ZeroCornerSize),
    small = androidx.compose.foundation.shape.RoundedCornerShape(ZeroCornerSize),
    medium = androidx.compose.foundation.shape.RoundedCornerShape(ZeroCornerSize),
    large = androidx.compose.foundation.shape.RoundedCornerShape(ZeroCornerSize),
    extraLarge = androidx.compose.foundation.shape.RoundedCornerShape(ZeroCornerSize),
)

val LocalVtType = staticCompositionLocalOf { VtType }

@Composable
fun VidthequeTheme(content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalVtType provides VtType) {
        MaterialTheme(colorScheme = scheme, typography = materialTypography, shapes = square, content = content)
    }
}
