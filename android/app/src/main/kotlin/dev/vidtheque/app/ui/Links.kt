package dev.vidtheque.app.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

/** Hand a link to whichever app takes it; false when none does (no browser, a locked-down profile). */
fun Context.openLink(uri: Uri): Boolean = try {
    startActivity(Intent(Intent.ACTION_VIEW, uri))
    true
} catch (_: ActivityNotFoundException) {
    false
}

const val NO_APP = "No app on this phone opens that link."
