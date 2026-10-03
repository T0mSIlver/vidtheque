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

/** One verdict as the server pushed it (push/notify.py), drawn as one notification. */
object VerdictNotifications {
    const val CHANNEL = "verdicts"
    const val EXTRA_VIDEO = "dev.vidtheque.app.VIDEO"

    fun createChannel(context: Context) {
        val channel = NotificationChannel(CHANNEL, "New verdicts", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "A new video from a channel you follow, judged worth your time."
        }
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
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
        still(context, videoId)?.let { builder.setLargeIcon(it) }
        data["moment_url"]?.takeIf { it.startsWith("https://") }?.let { url ->
            val watch = PendingIntent.getActivity(context, id + 1, Intent(Intent.ACTION_VIEW, Uri.parse(url)), PendingIntent.FLAG_IMMUTABLE)
            builder.addAction(0, "Best moment", watch)
        }
        if (NotificationManagerCompat.from(context).areNotificationsEnabled()) {
            @Suppress("MissingPermission") // checked just above
            NotificationManagerCompat.from(context).notify(id, builder.build())
        }
    }

    /** The video's still beside the text; a slow fetch leaves it out rather than holding the push. */
    private suspend fun still(context: Context, videoId: String): Bitmap? = withTimeoutOrNull(8_000) {
        val result = SingletonImageLoader.get(context).execute(ImageRequest.Builder(context).data(thumbnail(videoId)).allowHardware(false).build())
        (result as? SuccessResult)?.image?.toBitmap()
    }
}
