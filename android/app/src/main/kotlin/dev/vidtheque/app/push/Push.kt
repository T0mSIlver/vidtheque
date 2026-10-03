package dev.vidtheque.app.push

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.vidtheque.app.BuildConfig
import dev.vidtheque.app.data.Api
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.tasks.await
import javax.inject.Inject
import javax.inject.Singleton

private val Context.pushPrefs by preferencesDataStore("push")
private val ON = booleanPreferencesKey("on")

/**
 * Whether this phone gets verdicts, and its FCM registration with the instance.
 * The threshold and the rule are the server's (companion.md §6); the app only
 * says "this phone, yes or no".
 */
@Singleton
class Push @Inject constructor(@ApplicationContext private val context: Context, private val api: Api) {
    /** A build without the Firebase project's values has no push at all. */
    val available: Boolean get() = BuildConfig.FIREBASE_APP_ID.isNotEmpty()

    val on: Flow<Boolean> = context.pushPrefs.data.map { it[ON] == true }

    suspend fun enable() {
        // Auto-init is off in the manifest, so no token exists until the reader asks for push.
        FirebaseMessaging.getInstance().isAutoInitEnabled = true
        val token = FirebaseMessaging.getInstance().token.await()
        api.registerDevice(token)
        context.pushPrefs.edit { it[ON] = true }
    }

    /** Off, or signing out: the instance forgets the token, and FCM retires it. */
    suspend fun disable() {
        if (!available || !on.first()) return
        context.pushPrefs.edit { it[ON] = false }
        val token = runCatching { FirebaseMessaging.getInstance().token.await() }.getOrNull() ?: return
        runCatching { api.forgetDevice(token) }
        runCatching { FirebaseMessaging.getInstance().deleteToken().await() }
        FirebaseMessaging.getInstance().isAutoInitEnabled = false
    }

    /** FCM rotated the token: re-register it if this phone is on. */
    suspend fun tokenRotated(token: String) {
        if (on.first()) api.registerDevice(token)
    }

    companion object {
        /** No google-services plugin: the project's identity comes from the build. */
        fun init(context: Context) {
            if (BuildConfig.FIREBASE_APP_ID.isEmpty() || FirebaseApp.getApps(context).isNotEmpty()) return
            FirebaseApp.initializeApp(
                context,
                FirebaseOptions.Builder()
                    .setApplicationId(BuildConfig.FIREBASE_APP_ID)
                    .setApiKey(BuildConfig.FIREBASE_API_KEY)
                    .setProjectId(BuildConfig.FIREBASE_PROJECT_ID)
                    .setGcmSenderId(BuildConfig.FIREBASE_PROJECT_NUMBER)
                    .build(),
            )
        }
    }
}
