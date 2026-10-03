package dev.vidtheque.app.ui.theme

import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.MaterialExpressiveTheme
import androidx.compose.material3.MotionScheme
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

// A dark tonal scheme seeded from vidtheque's gold (#E7B455, Material's tonal-spot
// algorithm), with the brand gold itself kept as primary. DESIGN.md, the Android app.
private val dark = darkColorScheme(
    primary = Color(0xFFE7B455),
    onPrimary = Color(0xFF2A1D00),
    primaryContainer = Color(0xFF674E1F),
    onPrimaryContainer = Color(0xFFFFDFAB),
    secondary = Color(0xFFDAC3A0),
    onSecondary = Color(0xFF4D3E24),
    secondaryContainer = Color(0xFF48391F),
    onSecondaryContainer = Color(0xFFD3BC9A),
    // Blue, not the seed's peach: it marks the one thing that is not gold's to say,
    // a verdict from outside your profile (companion.md §3.2, exploration).
    tertiary = Color(0xFFA6CAF5),
    onTertiary = Color(0xFF0A3150),
    tertiaryContainer = Color(0xFF23415F),
    onTertiaryContainer = Color(0xFFD2E4FF),
    background = Color(0xFF110E08),
    onBackground = Color(0xFFF2E3D1),
    surface = Color(0xFF110E08),
    onSurface = Color(0xFFF2E3D1),
    surfaceVariant = Color(0xFF2C2519),
    onSurfaceVariant = Color(0xFFB6A998),
    outline = Color(0xFF7F7464),
    outlineVariant = Color(0xFF504739),
    surfaceContainerLowest = Color(0xFF0B0905),
    surfaceContainerLow = Color(0xFF17130B),
    surfaceContainer = Color(0xFF1E1910),
    surfaceContainerHigh = Color(0xFF251F15),
    surfaceContainerHighest = Color(0xFF2C2519),
    surfaceBright = Color(0xFF332B1F),
    surfaceDim = Color(0xFF110E08),
    inverseSurface = Color(0xFFFFF8F3),
    inverseOnSurface = Color(0xFF5A544C),
    inversePrimary = Color(0xFF755B2A),
    error = Color(0xFFFFB4AB),
    onError = Color(0xFF690005),
    errorContainer = Color(0xFF93000A),
    onErrorContainer = Color(0xFFFFDAD6),
)

// The same tonal palettes at the light scheme's tones; gold drops to tone 40 as primary
// so it reads on a light surface, and the brand gold becomes the inverse primary.
private val light = lightColorScheme(
    primary = Color(0xFF7A5916),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFFFDEA6),
    onPrimaryContainer = Color(0xFF5E4200),
    secondary = Color(0xFF6B5D3F),
    onSecondary = Color(0xFFFFFFFF),
    secondaryContainer = Color(0xFFF4E0BB),
    onSecondaryContainer = Color(0xFF52452A),
    tertiary = Color(0xFF3B6090),
    onTertiary = Color(0xFFFFFFFF),
    tertiaryContainer = Color(0xFFD2E4FF),
    onTertiaryContainer = Color(0xFF1F4876),
    background = Color(0xFFFFF8F2),
    onBackground = Color(0xFF1F1B13),
    surface = Color(0xFFFFF8F2),
    onSurface = Color(0xFF1F1B13),
    surfaceVariant = Color(0xFFEDE1CF),
    onSurfaceVariant = Color(0xFF4D4639),
    outline = Color(0xFF7F7667),
    outlineVariant = Color(0xFFD0C5B4),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFFAF2E7),
    surfaceContainer = Color(0xFFF4ECE1),
    surfaceContainerHigh = Color(0xFFEEE7DB),
    surfaceContainerHighest = Color(0xFFE9E1D6),
    surfaceBright = Color(0xFFFFF8F2),
    surfaceDim = Color(0xFFE0D9CC),
    inverseSurface = Color(0xFF353027),
    inverseOnSurface = Color(0xFFF7EFE4),
    inversePrimary = Color(0xFFE7B455),
    error = Color(0xFFBA1A1A),
    onError = Color(0xFFFFFFFF),
    errorContainer = Color(0xFFFFDAD6),
    onErrorContainer = Color(0xFF93000A),
)

/**
 * The two colours a Material scheme has no role for: a profile entry the verdict
 * matched that you want more of (green) or less of (the scheme's error red).
 * The green is a tonal pair at the scheme's container tones (30/90 dark, 90/10 light).
 */
@Immutable
data class Tones(val up: Color, val onUp: Color, val down: Color, val onDown: Color)

private val darkTones = Tones(up = Color(0xFF1F4F2B), onUp = Color(0xFFB4F1BD), down = dark.errorContainer, onDown = dark.onErrorContainer)
private val lightTones = Tones(up = Color(0xFFBDEFC2), onUp = Color(0xFF0A3818), down = light.errorContainer, onDown = light.onErrorContainer)

val LocalTones = staticCompositionLocalOf { darkTones }

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun VidthequeTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalTones provides if (darkTheme) darkTones else lightTones) {
        MaterialExpressiveTheme(
            colorScheme = if (darkTheme) dark else light,
            motionScheme = MotionScheme.expressive(),
            typography = typography,
            content = content,
        )
    }
}
