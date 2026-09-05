package org.sheetmusicshelf.app

import android.os.Bundle

/**
 * What the catalogue is narrowed by, in one object.
 *
 * The same set the server understands, so a screen full of chips maps onto a
 * query string with nothing lost in between.
 */
data class Filters(
    val query: String = "",
    val composer: String = "",
    val form: String = "",
    val instrument: String = "",
    val key: String = "",
    val period: String = "",
    val collectionId: Int = 0,
    val sort: String = "composer",
) {
    /** Every facet that is set, in the order they are shown as chips. */
    val active: List<Pair<String, String>>
        get() = buildList {
            if (composer.isNotBlank()) add(COMPOSER to composer)
            if (form.isNotBlank()) add(FORM to form)
            if (instrument.isNotBlank()) add(INSTRUMENT to instrument)
            if (key.isNotBlank()) add(KEY to key)
            if (period.isNotBlank()) add(PERIOD to period)
        }

    val isEmpty: Boolean get() = active.isEmpty() && query.isBlank() && collectionId == 0

    fun with(facet: String, value: String): Filters = when (facet) {
        COMPOSER -> copy(composer = value)
        FORM -> copy(form = value)
        INSTRUMENT -> copy(instrument = value)
        KEY -> copy(key = value)
        PERIOD -> copy(period = value)
        else -> this
    }

    fun without(facet: String): Filters = with(facet, "")

    fun valueOf(facet: String): String = when (facet) {
        COMPOSER -> composer
        FORM -> form
        INSTRUMENT -> instrument
        KEY -> key
        PERIOD -> period
        else -> ""
    }

    fun cleared(): Filters = Filters(sort = sort)

    /** The query string, built the way the server reads it. */
    fun toQuery(limit: Int): String = buildString {
        append("?limit=").append(limit)
        append("&order=").append(sort)
        fun add(name: String, value: String) {
            if (value.isNotBlank()) {
                append('&').append(name).append('=')
                append(java.net.URLEncoder.encode(value, "UTF-8"))
            }
        }
        add("q", query)
        add("composer", composer)
        add("form", form)
        add("instrument", instrument)
        add("music_key", key)
        add("period", period)
        if (collectionId != 0) append("&collection_id=").append(collectionId)
    }

    fun into(bundle: Bundle): Bundle = bundle.apply {
        putString("query", query)
        putString(COMPOSER, composer)
        putString(FORM, form)
        putString(INSTRUMENT, instrument)
        putString(KEY, key)
        putString(PERIOD, period)
        putInt("collection", collectionId)
        putString("sort", sort)
    }

    companion object {
        const val COMPOSER = "composer"
        const val FORM = "form"
        const val INSTRUMENT = "instrument"
        const val KEY = "key"
        const val PERIOD = "period"
        const val COLLECTION = "collection"

        /** Facets in the order the filter screen lists them: broadest first. */
        val FACETS = listOf(COMPOSER, PERIOD, FORM, INSTRUMENT, KEY, COLLECTION)

        val SORTS = listOf(
            "composer" to "Composer",
            "title" to "Title",
            "recent" to "Recently added",
            "confidence" to "Least certain",
        )

        fun from(bundle: Bundle?): Filters {
            if (bundle == null) return Filters()
            return Filters(
                query = bundle.getString("query", ""),
                composer = bundle.getString(COMPOSER, ""),
                form = bundle.getString(FORM, ""),
                instrument = bundle.getString(INSTRUMENT, ""),
                key = bundle.getString(KEY, ""),
                period = bundle.getString(PERIOD, ""),
                collectionId = bundle.getInt("collection", 0),
                sort = bundle.getString("sort", "composer"),
            )
        }

        fun label(facet: String): String = when (facet) {
            COMPOSER -> "Composer"
            FORM -> "Form"
            INSTRUMENT -> "Scored for"
            KEY -> "Key"
            PERIOD -> "Period"
            COLLECTION -> "Collection"
            else -> facet
        }
    }
}

/** One value a facet can take, and how many pieces are behind it. */
data class FacetValue(val value: String, val count: Int, val id: Int = 0) {
    val display: String get() = "$value  ($count)"
}
