package dev.vidtheque.app.data

import android.content.Context
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import dev.vidtheque.app.auth.Clock
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** A link handed to YouTube: when, the `watch` signal it wrote, and the time away once the app came back. */
data class HandOff(val startedMs: Long, val signalId: Long = 0, val awayS: Double? = null)

interface HandOffStore {
    fun read(): HandOff?
    fun write(handOff: HandOff?)
}

/** In preferences, so a process YouTube pushed out of memory still reports on its next start. */
class PrefsHandOffStore @Inject constructor(@ApplicationContext context: Context) : HandOffStore {
    private val prefs = context.getSharedPreferences("watch", Context.MODE_PRIVATE)

    override fun read(): HandOff? {
        val started = prefs.getLong("started", 0)
        if (started == 0L) return null
        val away = prefs.getFloat("away", -1f)
        return HandOff(started, prefs.getLong("signal", 0), away.takeIf { it >= 0 }?.toDouble())
    }

    override fun write(handOff: HandOff?) {
        val edit = prefs.edit().clear()
        if (handOff != null) {
            edit.putLong("started", handOff.startedMs).putLong("signal", handOff.signalId)
            handOff.awayS?.let { edit.putFloat("away", it.toFloat()) }
        }
        edit.commit()
    }
}

/**
 * There is no player here: a moment opens in YouTube. The time until the app is
 * back on screen is how long it was watched (companion.md §2.3); the server caps
 * it at the rest of the video.
 */
@Singleton
class WatchClock @Inject constructor(
    private val api: Api,
    private val store: HandOffStore,
    private val clock: Clock,
    private val scope: CoroutineScope,
) {
    /** Call once the link was handed to an app: it sends `watch` and starts the clock. */
    fun handOff(videoId: String, offsetS: Int) {
        val started = clock.nowMs()
        val unsent = synchronized(this) {
            store.read().also { store.write(HandOff(started)) }
        }?.takeIf { it.signalId != 0L && it.awayS != null }
        // A return the network lost last time goes out now rather than being overwritten.
        unsent?.let { scope.launch { runCatching { api.watched(it.signalId, it.awayS!!) } } }
        scope.launch {
            val id = runCatching { api.watch(videoId, offsetS) }.getOrNull() ?: return@launch
            synchronized(this@WatchClock) {
                // Back already, or another hand-off since: this one's return is no longer ours to send.
                if (store.read()?.startedMs == started) store.write(HandOff(started, id))
            }
        }
    }

    /** The app is on screen again. Sends the time away once; a lost network keeps it for the next start. */
    fun returned() {
        val pending = synchronized(this) {
            val now = store.read() ?: return
            // The `watch` never landed: back before its answer, or offline. Nothing to close.
            if (now.signalId == 0L) {
                store.write(null)
                return
            }
            now.copy(awayS = now.awayS ?: ((clock.nowMs() - now.startedMs) / 1000.0).coerceAtLeast(0.0)).also(store::write)
        }
        scope.launch {
            val sent = try {
                api.watched(pending.signalId, pending.awayS!!)
                true
            } catch (_: IOException) {
                false
            } catch (_: ApiException) {
                // The server answered; asking again would get the same answer.
                true
            }
            if (sent) synchronized(this@WatchClock) { if (store.read()?.startedMs == pending.startedMs) store.write(null) }
        }
    }
}

@Module
@InstallIn(SingletonComponent::class)
abstract class WatchModule {
    @Binds
    abstract fun handOffStore(store: PrefsHandOffStore): HandOffStore
}
