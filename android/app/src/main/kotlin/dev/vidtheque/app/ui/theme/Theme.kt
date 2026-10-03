package dev.vidtheque.app.ui.theme

import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.MaterialExpressiveTheme
import androidx.compose.material3.MotionScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// A dark tonal scheme seeded from vidtheque's gold (#E7B455, Material's tonal-spot
// algorithm), with the brand gold itself kept as primary. DESIGN.md, the Android app.
private val scheme = darkColorScheme(
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

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun VidthequeTheme(content: @Composable () -> Unit) {
    MaterialExpressiveTheme(
        colorScheme = scheme,
        motionScheme = MotionScheme.expressive(),
        typography = typography,
        content = content,
    )
}
