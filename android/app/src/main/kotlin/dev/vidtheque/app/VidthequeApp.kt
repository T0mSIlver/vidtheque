package dev.vidtheque.app

import android.app.Application
import dagger.hilt.android.HiltAndroidApp
import dev.vidtheque.app.push.Push
import dev.vidtheque.app.push.VerdictNotifications

@HiltAndroidApp
class VidthequeApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Push.init(this)
        VerdictNotifications.createChannel(this)
    }
}
