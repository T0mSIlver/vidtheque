package dev.vidtheque.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.sp
import dev.vidtheque.app.R

// Roboto Flex, subset to Latin with the wght, wdth and opsz axes kept. Each weight
// is its own Font so the axis lands exactly; opsz follows the size, as Flex intends.
private fun flex(w: Int) = Font(
    R.font.roboto_flex,
    FontWeight(w),
    variationSettings = FontVariation.Settings(FontVariation.weight(w)),
)

val RobotoFlex = FontFamily(flex(400), flex(500), flex(600), flex(700), flex(800))

/** The wordmark keeps the web's face: the logo is the word, in Archivo 500. */
val Archivo = FontFamily(
    Font(R.font.archivo, FontWeight(500), variationSettings = FontVariation.Settings(FontVariation.weight(500))),
)

private fun style(size: TextUnit, line: TextUnit, weight: Int, tracking: TextUnit = 0.sp) =
    TextStyle(fontFamily = RobotoFlex, fontWeight = FontWeight(weight), fontSize = size, lineHeight = line, letterSpacing = tracking)

// Material 3's type scale (sizes and line heights as specified), set in Roboto Flex;
// the emphasized styles are the Expressive scale's heavier twins.
internal val typography = Typography(
    displayLarge = style(57.sp, 64.sp, 400, (-0.25).sp),
    displayMedium = style(45.sp, 52.sp, 400),
    displaySmall = style(36.sp, 44.sp, 400),
    headlineLarge = style(32.sp, 40.sp, 400),
    headlineMedium = style(28.sp, 36.sp, 400),
    headlineSmall = style(24.sp, 32.sp, 400),
    titleLarge = style(22.sp, 28.sp, 400),
    titleMedium = style(16.sp, 24.sp, 500, 0.15.sp),
    titleSmall = style(14.sp, 20.sp, 500, 0.1.sp),
    bodyLarge = style(16.sp, 24.sp, 400, 0.5.sp),
    bodyMedium = style(14.sp, 20.sp, 400, 0.25.sp),
    bodySmall = style(12.sp, 16.sp, 400, 0.4.sp),
    labelLarge = style(14.sp, 20.sp, 500, 0.1.sp),
    labelMedium = style(12.sp, 16.sp, 500, 0.5.sp),
    labelSmall = style(11.sp, 16.sp, 500, 0.5.sp),
    displayLargeEmphasized = style(57.sp, 64.sp, 600, (-0.25).sp),
    displayMediumEmphasized = style(45.sp, 52.sp, 600),
    displaySmallEmphasized = style(36.sp, 44.sp, 600),
    headlineLargeEmphasized = style(32.sp, 40.sp, 600),
    headlineMediumEmphasized = style(28.sp, 36.sp, 600),
    headlineSmallEmphasized = style(24.sp, 32.sp, 600),
    titleLargeEmphasized = style(22.sp, 28.sp, 600),
    titleMediumEmphasized = style(16.sp, 24.sp, 700, 0.15.sp),
    titleSmallEmphasized = style(14.sp, 20.sp, 700, 0.1.sp),
    bodyLargeEmphasized = style(16.sp, 24.sp, 500, 0.5.sp),
    bodyMediumEmphasized = style(14.sp, 20.sp, 500, 0.25.sp),
    bodySmallEmphasized = style(12.sp, 16.sp, 500, 0.4.sp),
    labelLargeEmphasized = style(14.sp, 20.sp, 700, 0.1.sp),
    labelMediumEmphasized = style(12.sp, 16.sp, 700, 0.5.sp),
    labelSmallEmphasized = style(11.sp, 16.sp, 700, 0.5.sp),
)
