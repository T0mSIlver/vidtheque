package dev.vidtheque.app.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.graphics.drawable.toBitmap
import coil3.SingletonImageLoader
import coil3.request.ImageRequest
import coil3.request.SuccessResult
import coil3.request.allowHardware
import coil3.toBitmap
import dev.vidtheque.app.MainActivity
import dev.vidtheque.app.R
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.scoreWord
import dev.vidtheque.app.ui.thumbnail
import kotlinx.coroutines.withTimeoutOrNull

/** What the server pushes (push/notify.py), drawn here: one notification per verdict, grouped, and the Sunday brief. */
object VerdictNotifications {
    const val CHANNEL = "verdicts"
    const val BRIEF_CHANNEL = "brief"
    const val EXTRA_VIDEO = "dev.vidtheque.app.VIDEO"
    const val EXTRA_BRIEF = "dev.vidtheque.app.BRIEF"

    // Every verdict at the threshold is pushed, uncapped (companion.md §6); a busy
    // upload day collapses into one stack under this group's summary.
    private const val GROUP = "dev.vidtheque.app.VERDICTS"
    private const val SUMMARY_ID = 1
    private const val BRIEF_ID = 2

    fun createChannel(context: Context) {
        val channel = NotificationChannel(CHANNEL, "New verdicts", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "A new video from a channel you follow, judged worth your time."
        }
        val brief = NotificationChannel(BRIEF_CHANNEL, "Weekly brief", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Sunday morning: your week's best videos, and what changed."
        }
        context.getSystemService(NotificationManager::class.java).createNotificationChannels(listOf(channel, brief))
    }

    /** Sunday's one notification: the week's brief is ready (companion.md §6.1). */
    fun showBrief(context: Context, data: Map<String, String>) {
        val open = PendingIntent.getActivity(
            context, BRIEF_ID,
            Intent(context, MainActivity::class.java).putExtra(EXTRA_BRIEF, true).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(context, BRIEF_CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setColor(0xFFE7B455.toInt())
            .setContentTitle("Your week")
            .setContentText(data["line"].orEmpty())
            .setStyle(NotificationCompat.BigTextStyle().bigText(data["line"].orEmpty()))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        post(context, BRIEF_ID, notification)
    }

    suspend fun show(context: Context, data: Map<String, String>) {
        val videoId = data["video_id"] ?: return
        val score = data["score"]?.toIntOrNull() ?: return
        val id = videoId.hashCode()
        val channel = data["channel"].orEmpty()
        // "Theo · 3 · his eval setup, 14:02" (companion.md §6).
        val moment = data["moment_s"]?.toDoubleOrNull()?.let { s -> "${data["moment_why"].orEmpty()}, ${duration(s)}" }
        val line = listOfNotNull(channel.ifEmpty { null }, "$score · ${scoreWord(score)}", data["reason"]).joinToString(" · ")
        val open = PendingIntent.getActivity(
            context, id,
            Intent(context, MainActivity::class.java).putExtra(EXTRA_VIDEO, videoId).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val dismissed = PendingIntent.getBroadcast(
            context, id,
            Intent(context, DismissReceiver::class.java).putExtra(EXTRA_VIDEO, videoId),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setColor(0xFFE7B455.toInt())
            .setContentTitle(data["title"].orEmpty())
            .setContentText(line)
            .setStyle(NotificationCompat.BigTextStyle().bigText(listOfNotNull(line, moment?.let { "Best moment: $it" }).joinToString("\n")))
            .setContentIntent(open)
            .setDeleteIntent(dismissed)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_RECOMMENDATION)
            .setGroup(GROUP)
        still(context, videoId)?.let { builder.setLargeIcon(it) }
        data["moment_url"]?.takeIf { it.startsWith("https://") }?.let { url ->
            val watch = PendingIntent.getActivity(context, id + 1, Intent(Intent.ACTION_VIEW, Uri.parse(url)), PendingIntent.FLAG_IMMUTABLE)
            builder.addAction(0, "Best moment", watch)
        }
        post(context, id, builder.build())
        summarize(context, id, data["title"].orEmpty())
    }

    /**
     * The group's summary: the count, and the newest titles when the stack is folded.
     * The one just posted may not be listed yet (notify is asynchronous), so it is added by hand.
     */
    private fun summarize(context: Context, id: Int, title: String) {
        val others = context.getSystemService(NotificationManager::class.java).activeNotifications
            .filter { it.notification.group == GROUP && it.id != SUMMARY_ID && it.id != id }
            .sortedByDescending { it.postTime }
        if (others.isEmpty()) return
        val count = others.size + 1
        val inbox = NotificationCompat.InboxStyle().setSummaryText("$count new verdicts").addLine(title)
        others.take(4).forEach { inbox.addLine(it.notification.extras.getCharSequence(NotificationCompat.EXTRA_TITLE)) }
        val summary = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setColor(0xFFE7B455.toInt())
            .setContentTitle("$count new verdicts")
            .setStyle(inbox)
            .setGroup(GROUP)
            .setGroupSummary(true)
            // The stack buzzes once per verdict already; the summary stays quiet.
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setContentIntent(PendingIntent.getActivity(context, SUMMARY_ID, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))
            .setAutoCancel(true)
            .build()
        post(context, SUMMARY_ID, summary)
    }

    private fun post(context: Context, id: Int, notification: android.app.Notification) {
        if (NotificationManagerCompat.from(context).areNotificationsEnabled()) {
            @Suppress("MissingPermission") // checked just above
            NotificationManagerCompat.from(context).notify(id, notification)
        }
    }

    /** The video's still beside the text; a slow fetch leaves it out rather than holding the push. */
    private suspend fun still(context: Context, videoId: String): Bitmap? = withTimeoutOrNull(8_000) {
        val result = SingletonImageLoader.get(context).execute(ImageRequest.Builder(context).data(thumbnail(videoId)).allowHardware(false).build())
        (result as? SuccessResult)?.image?.toBitmap()
    }
}
