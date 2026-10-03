package dev.vidtheque.app.push

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import dev.vidtheque.app.data.Api
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import javax.inject.Inject

@AndroidEntryPoint
class VerdictMessagingService : FirebaseMessagingService() {
    @Inject lateinit var push: Push

    // FCM calls this on its own worker thread with time to finish; blocking here is the contract.
    override fun onMessageReceived(message: RemoteMessage) {
        runBlocking { VerdictNotifications.show(this@VerdictMessagingService, message.data) }
    }

    override fun onNewToken(token: String) {
        runBlocking { runCatching { push.tokenRotated(token) } }
    }
}

/** A verdict swiped away unopened is a weak "not for me" (companion.md §2.3). */
@AndroidEntryPoint
class DismissReceiver : BroadcastReceiver() {
    @Inject lateinit var api: Api
    @Inject lateinit var scope: CoroutineScope

    override fun onReceive(context: Context, intent: Intent) {
        val videoId = intent.getStringExtra(VerdictNotifications.EXTRA_VIDEO) ?: return
        val pending = goAsync()
        scope.launch {
            try {
                runCatching { api.signal("dismiss", videoId) }
            } finally {
                pending.finish()
            }
        }
    }
}
