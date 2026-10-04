package dev.vidtheque.app.share

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import dagger.hilt.android.AndroidEntryPoint
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.Shared
import java.io.IOException
import javax.inject.Inject
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

private val URL = Regex("""https?://\S+""")

/** The first link in a share's text; YouTube shares a bare `youtu.be` link, other apps add a title. */
fun sharedLink(text: String?): String? = text?.let { URL.find(it)?.value }

/** What a share's answer says, in one line for a toast. */
fun sharedLine(shared: Shared): String {
    val start = if (shared.indexed) "Already indexed." else "Indexing it."
    return when (shared.miss) {
        true -> "$start A miss: ${shared.why}."
        false -> "The feed had it: ${shared.why}."
        null -> "$start Whether the feed missed it is known once it is judged."
    }
}

/** "Found it elsewhere": a YouTube link shared from any app, indexed and counted (companion.md §6). Draws nothing. */
@AndroidEntryPoint
class ShareActivity : ComponentActivity() {
    @Inject lateinit var api: Api

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val link = sharedLink(intent.takeIf { it.action == Intent.ACTION_SEND }?.getStringExtra(Intent.EXTRA_TEXT))
        if (link == null) {
            done("No link in what was shared.")
            return
        }
        lifecycleScope.launch {
            val line = try {
                withTimeoutOrNull(15_000) { sharedLine(api.share(link)) } ?: "The instance did not answer."
            } catch (e: ApiException) {
                e.message ?: "The instance refused it."
            } catch (e: IOException) {
                if (api.base.isEmpty()) "Sign in to your vidtheque instance first." else "The instance did not answer."
            }
            done(line)
        }
    }

    private fun done(line: String) {
        Toast.makeText(applicationContext, line, Toast.LENGTH_LONG).show()
        finish()
    }
}
