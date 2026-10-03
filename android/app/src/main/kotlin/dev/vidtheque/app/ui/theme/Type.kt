package dev.vidtheque.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import dev.vidtheque.app.R

// Variable faces: each rung is its own Font so the wght axis lands on the exact
// value of DESIGN.md's weight law (200 / 250 / 340 / 500 / 600; mono 400 / 600 / 700).
private fun archivo(w: Int) = Font(R.font.archivo, FontWeight(w), variationSettings = FontVariation.Settings(FontVariation.weight(w)))
private fun mono(w: Int) = Font(R.font.jetbrains_mono, FontWeight(w), variationSettings = FontVariation.Settings(FontVariation.weight(w)))

val Archivo = FontFamily(archivo(200), archivo(250), archivo(340), archivo(500), archivo(600))
val JetBrainsMono = FontFamily(mono(400), mono(600), mono(700))

private val trim = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None)

/** The ladder at the phone breakpoint (below --bp-hand: body 15). Mono is the machine's voice. */
object VtType {
    val display = TextStyle(fontFamily = Archivo, fontWeight = FontWeight(200), fontSize = 34.sp, lineHeight = 36.sp, letterSpacing = (-0.03).em)
    val headline = TextStyle(fontFamily = Archivo, fontWeight = FontWeight(250), fontSize = 22.sp, lineHeight = 27.sp, letterSpacing = (-0.015).em)
    val body = TextStyle(fontFamily = Archivo, fontWeight = FontWeight(340), fontSize = 15.sp, lineHeight = 22.sp)
    val prose = TextStyle(fontFamily = Archivo, fontWeight = FontWeight(340), fontSize = 15.5.sp, lineHeight = 24.sp)
    val cell = TextStyle(fontFamily = Archivo, fontWeight = FontWeight(340), fontSize = 14.sp, lineHeight = 19.sp)
    val cellStrong = cell.copy(fontWeight = FontWeight(500))
    val action = TextStyle(fontFamily = Archivo, fontWeight = FontWeight(600), fontSize = 15.sp, lineHeight = 20.sp)
    val wordmark = TextStyle(fontFamily = Archivo, fontWeight = FontWeight(500), fontSize = 15.sp, letterSpacing = (-0.035).em)
    val machine = TextStyle(fontFamily = JetBrainsMono, fontWeight = FontWeight(400), fontSize = 13.sp, lineHeight = 18.sp, fontFeatureSettings = "tnum, liga 0, calt 0")
    val machineSm = machine.copy(fontSize = 11.sp, lineHeight = 15.sp)
    val label = TextStyle(fontFamily = JetBrainsMono, fontWeight = FontWeight(600), fontSize = 10.sp, lineHeight = 14.sp, letterSpacing = 0.19.em, fontFeatureSettings = "tnum, liga 0, calt 0", lineHeightStyle = trim)
    val labelSm = label.copy(fontSize = 9.5.sp, letterSpacing = 0.17.em)
}

internal val materialTypography = Typography(
    displaySmall = VtType.display,
    headlineSmall = VtType.headline,
    titleMedium = VtType.cellStrong,
    bodyLarge = VtType.body,
    bodyMedium = VtType.cell,
    labelLarge = VtType.action,
    labelMedium = VtType.label,
    labelSmall = VtType.labelSm,
)
