package org.sheetmusicshelf.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import okhttp3.Cache
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * The server, as this app needs it.
 *
 * Pages arrive as images the server has already rendered, rather than as a PDF
 * this app renders itself. Most of the library has no text layer, so the server
 * is rendering anyway and caches the result by content hash; asking for a page
 * at the width the screen actually has beats pulling a 50 MB scan over a VPN
 * to show one page of it.
 */
class Api(context: Context) {

    private val prefs = Prefs(context)

    private val http = OkHttpClient.Builder()
        // Page images are immutable for a given piece, page and width, so the
        // server marks them cacheable and this keeps them across launches.
        .cache(Cache(context.cacheDir.resolve("http"), 96L * 1024 * 1024))
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    class NotConfigured : IOException("Set the server address and token first")

    private fun base(): String {
        val url = prefs.serverUrl.trim().trimEnd('/')
        if (url.isEmpty() || prefs.token.isBlank()) throw NotConfigured()
        return url
    }

    private fun request(path: String): Request.Builder =
        Request.Builder()
            .url(base() + path)
            .header("Authorization", "Bearer " + prefs.token.trim())

    private fun body(response: okhttp3.Response): String {
        response.use {
            val text = it.body?.string().orEmpty()
            if (!it.isSuccessful) {
                throw IOException(
                    when (it.code) {
                        401 -> "The token was refused. Check it in Settings."
                        403 -> "That token lacks the scope for this."
                        404 -> "Not found on the server."
                        else -> "Server said ${it.code}"
                    }
                )
            }
            return text
        }
    }

    /** The catalogue, narrowed. Empty filters return the first page of everything. */
    fun pieces(filters: Filters, limit: Int = 200): List<Piece> {
        val json = JSONArray(
            body(http.newCall(request("/api/v1/pieces" + filters.toQuery(limit)).build()).execute())
        )
        return (0 until json.length()).map { Piece.from(json.getJSONObject(it)) }
    }

    /**
     * What each facet can be narrowed to, with a count against every value.
     *
     * The counts are the point: they say whether a filter is worth tapping,
     * and a facet with nothing behind it is not offered at all.
     */
    fun facets(): Map<String, List<FacetValue>> {
        val json = JSONObject(body(http.newCall(request("/api/v1/facets").build()).execute()))
        val facets = HashMap<String, List<FacetValue>>()
        for (name in json.keys()) {
            val rows = json.optJSONArray(name) ?: continue
            facets[if (name == "collections") Filters.COLLECTION else name] =
                (0 until rows.length()).mapNotNull { index ->
                    val row = rows.optJSONArray(index) ?: return@mapNotNull null
                    if (name == "collections") {
                        // Collections come back as (id, name): the id is what
                        // the server filters on, the name is what to show.
                        FacetValue(row.optString(1), 0, row.optInt(0))
                    } else {
                        FacetValue(row.optString(0), row.optInt(1))
                    }
                }
        }
        return facets
    }

    fun piece(id: Int): Piece =
        Piece.from(JSONObject(body(http.newCall(request("/api/v1/pieces/$id").build()).execute())))

    /**
     * One rendered page, 1-based within the piece.
     *
     * Widths are a closed set on the server; anything else is snapped to the
     * nearest, so asking for the exact screen width is fine and wastes nothing.
     */
    fun page(pieceId: Int, page: Int, width: Int): Bitmap? {
        val response = http.newCall(request("/api/v1/pieces/$pieceId/pages/$page?width=$width").build()).execute()
        response.use {
            if (!it.isSuccessful) return null
            val bytes = it.body?.bytes() ?: return null
            return BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        }
    }

    fun annotations(pieceId: Int): Map<Int, List<Stroke>> {
        val json = JSONArray(body(http.newCall(request("/api/v1/pieces/$pieceId/annotations").build()).execute()))
        val layers = HashMap<Int, List<Stroke>>()
        for (i in 0 until json.length()) {
            val layer = json.getJSONObject(i)
            layers[layer.getInt("page")] = Stroke.listFrom(layer.getJSONArray("strokes"))
        }
        return layers
    }

    /** Replace one page's marks. An empty list clears the page. */
    fun putAnnotations(pieceId: Int, page: Int, strokes: List<Stroke>) {
        val payload = JSONObject()
            .put("page", page)
            .put("strokes", Stroke.toJson(strokes))
            .toString()
            .toRequestBody("application/json".toMediaType())
        body(http.newCall(request("/api/v1/pieces/$pieceId/annotations/$page").put(payload).build()).execute())
    }


    /**
     * The version of this app the server is offering, or null if none.
     *
     * The app compares it with its own and says nothing unless there is
     * something newer, so a tablet that is up to date is never nagged.
     */
    fun offeredVersion(): Offered? {
        val json = JSONObject(body(http.newCall(request("/api/v1/app/version").build()).execute()))
        if (!json.optBoolean("available")) return null
        return Offered(
            versionCode = json.optInt("versionCode"),
            versionName = if (json.isNull("versionName")) "" else json.optString("versionName"),
            url = base() + json.optString("url"),
        )
    }

    /** A cheap call that proves the address and token are both right. */
    fun check(): String {
        val json = JSONArray(body(http.newCall(request("/api/v1/pieces?limit=1").build()).execute()))
        return if (json.length() > 0) "Connected" else "Connected, but the catalogue is empty"
    }
}

/** A build the server has, described well enough to decide about. */
data class Offered(val versionCode: Int, val versionName: String, val url: String)


data class Piece(
    val id: Int,
    val title: String,
    val composer: String,
    val catalog: String,
    val musicKey: String,
    val pageStart: Int,
    val pageEnd: Int,
) {
    /** Pages are addressed 1..pageCount within the piece, not within the file. */
    val pageCount: Int get() = (pageEnd - pageStart + 1).coerceAtLeast(1)

    val subtitle: String
        get() = listOf(composer, catalog, musicKey)
            .filter { it.isNotBlank() }
            .joinToString("  ·  ")

    companion object {
        /**
         * `optString` is not safe here.
         *
         * The server sends an absent value as JSON null, and Android's
         * org.json turns that into the *string* "null" rather than the
         * fallback -- so a piece with no catalogue number would read
         * "Abba - null - null" on the list. Check for null first.
         */
        private fun text(json: JSONObject, key: String): String =
            if (json.isNull(key)) "" else json.optString(key, "")

        fun from(json: JSONObject) = Piece(
            id = json.getInt("id"),
            title = text(json, "title").ifBlank { "Untitled" },
            composer = text(json, "composer_name"),
            catalog = text(json, "catalog_display"),
            musicKey = text(json, "music_key"),
            pageStart = json.optInt("page_start", 1),
            pageEnd = json.optInt("page_end", 1),
        )
    }
}

/**
 * One continuous mark, in the server's own terms.
 *
 * Points are 0..1 of the page box and width is a fraction of the page width,
 * so a mark made on a phone lands in the same place, at the same thickness, on
 * a tablet -- and on the web reader, which writes these same rows.
 */
data class Stroke(
    val tool: String,
    val color: String,
    val width: Float,
    val points: List<Pair<Float, Float>>,
) {
    companion object {
        fun listFrom(array: JSONArray): List<Stroke> = (0 until array.length()).map { i ->
            val json = array.getJSONObject(i)
            val raw = json.getJSONArray("points")
            Stroke(
                tool = if (json.isNull("tool")) "pen" else json.optString("tool", "pen"),
                color = if (json.isNull("color")) "#c0392b" else json.optString("color", "#c0392b"),
                width = json.optDouble("width", 0.004).toFloat(),
                points = (0 until raw.length()).map { p ->
                    val point = raw.getJSONArray(p)
                    point.getDouble(0).toFloat() to point.getDouble(1).toFloat()
                },
            )
        }

        fun toJson(strokes: List<Stroke>): JSONArray {
            val array = JSONArray()
            for (stroke in strokes) {
                val points = JSONArray()
                for ((x, y) in stroke.points) {
                    points.put(JSONArray().put(x.toDouble()).put(y.toDouble()))
                }
                array.put(
                    JSONObject()
                        .put("tool", stroke.tool)
                        .put("color", stroke.color)
                        .put("width", stroke.width.toDouble())
                        .put("points", points)
                )
            }
            return array
        }
    }
}
