package org.sheetmusicshelf.app

import android.content.Context

/** Where the server is and how to prove who we are. */
class Prefs(context: Context) {

    private val store = context.applicationContext
        .getSharedPreferences("sheetmusicshelf", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = store.getString(SERVER, "") ?: ""
        set(value) = store.edit().putString(SERVER, normalise(value)).apply()

    var token: String
        get() = store.getString(TOKEN, "") ?: ""
        set(value) = store.edit().putString(TOKEN, value.trim()).apply()

    val configured: Boolean get() = serverUrl.isNotBlank() && token.isNotBlank()

    /** Accept what a person would actually type: no scheme, a trailing slash. */
    private fun normalise(raw: String): String {
        var url = raw.trim().trimEnd('/')
        if (url.isNotEmpty() && !url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://$url"
        }
        return url
    }

    private companion object {
        const val SERVER = "server_url"
        const val TOKEN = "api_token"
    }
}
