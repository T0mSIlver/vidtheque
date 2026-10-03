package dev.vidtheque.app.net

import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import dev.vidtheque.app.auth.Clock
import dev.vidtheque.app.auth.KeystoreTokenStore
import dev.vidtheque.app.auth.Session
import dev.vidtheque.app.auth.TokenStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import okhttp3.Authenticator
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import java.io.IOException
import java.util.concurrent.TimeUnit
import javax.inject.Named
import javax.inject.Singleton

/** Bearer on every call, and one refresh-and-retry when the server answers 401. */
fun authorized(base: OkHttpClient, session: Session): OkHttpClient = base.newBuilder()
    .addInterceptor(Interceptor { chain ->
        // OkHttp runs interceptors on its own threads, so blocking here blocks no UI.
        // A failed refresh surfaces as the call's IOException, like any network failure.
        val token = runBlocking { runCatching { session.accessToken() } }.getOrElse { throw IOException(it.message, it) }
        val request = chain.request()
        chain.proceed(if (token == null) request else request.newBuilder().header("Authorization", "Bearer $token").build())
    })
    .authenticator(Authenticator { _, response ->
        val rejected = response.request.header("Authorization")?.removePrefix("Bearer ")
        if (rejected == null || response.priorResponse != null) return@Authenticator null
        val next = runBlocking { runCatching { session.afterUnauthorized(rejected) }.getOrNull() } ?: return@Authenticator null
        response.request.newBuilder().header("Authorization", "Bearer $next").build()
    })
    .build()

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {
    @Provides
    @Singleton
    fun http(): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    @Provides
    @Singleton
    @Named("api")
    fun api(base: OkHttpClient, session: Session): OkHttpClient = authorized(base, session)

    @Provides
    @Singleton
    fun scope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Provides
    fun clock(): Clock = Clock(System::currentTimeMillis)

    @Provides
    fun tokenStore(store: KeystoreTokenStore): TokenStore = store
}
