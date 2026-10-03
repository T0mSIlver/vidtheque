package dev.vidtheque.app.auth

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.first
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.inject.Inject
import javax.inject.Singleton

@Serializable
data class Tokens(val access: String, val refresh: String?, val expiresAtMs: Long)

/** Where the tokens live between launches. */
interface TokenStore {
    suspend fun load(): Tokens?
    suspend fun save(tokens: Tokens)
    suspend fun clear()
}

private val Context.vault: DataStore<Preferences> by preferencesDataStore("session")

/**
 * The tokens as one AES-GCM blob in DataStore, under a key that never leaves the
 * Android Keystore. EncryptedSharedPreferences is deprecated; this is all it did.
 */
@Singleton
class KeystoreTokenStore @Inject constructor(@ApplicationContext private val context: Context) : TokenStore {
    private val entry = stringPreferencesKey("tokens")

    override suspend fun load(): Tokens? {
        val sealed = context.vault.data.first()[entry] ?: return null
        // A blob the key cannot open (restored backup, reset keystore) is a signed-out session.
        return runCatching { Json.decodeFromString<Tokens>(open(sealed)) }.getOrNull()
    }

    override suspend fun save(tokens: Tokens) {
        context.vault.edit { it[entry] = seal(Json.encodeToString(Tokens.serializer(), tokens)) }
    }

    override suspend fun clear() {
        context.vault.edit { it.remove(entry) }
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
        }.generateKey()
    }

    private fun seal(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, key()) }
        return Base64.getEncoder().encodeToString(cipher.iv + cipher.doFinal(plain.toByteArray()))
    }

    private fun open(sealed: String): String {
        val bytes = Base64.getDecoder().decode(sealed)
        val cipher = Cipher.getInstance(TRANSFORM).apply {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, IV_BYTES))
        }
        return String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES))
    }

    private companion object {
        const val ALIAS = "vidtheque.session"
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}
