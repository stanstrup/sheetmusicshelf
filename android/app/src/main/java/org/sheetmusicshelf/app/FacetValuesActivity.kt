package org.sheetmusicshelf.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.sheetmusicshelf.app.databinding.ActivityFacetValuesBinding

/**
 * Choosing one value of one facet.
 *
 * The values arrive with the intent rather than being fetched again: the
 * filter screen already has them, and a list that waits on the network after
 * every tap makes browsing feel like searching.
 *
 * There are 200 composers, so the list filters as you type. Forms and keys are
 * short enough that the box is just ignored.
 */
class FacetValuesActivity : AppCompatActivity() {

    private lateinit var views: ActivityFacetValuesBinding
    private lateinit var adapter: ValueAdapter
    private var facet = ""
    private var all: List<FacetValue> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        views = ActivityFacetValuesBinding.inflate(layoutInflater)
        setContentView(views.root)
        setSupportActionBar(views.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        facet = intent.getStringExtra(EXTRA_FACET).orEmpty()
        title = intent.getStringExtra(EXTRA_LABEL) ?: getString(R.string.app_name)
        val chosen = intent.getStringExtra(EXTRA_CHOSEN).orEmpty()

        val values = intent.getStringArrayListExtra(EXTRA_VALUES).orEmpty()
        val counts = intent.getIntegerArrayListExtra(EXTRA_COUNTS).orEmpty()
        val ids = intent.getIntegerArrayListExtra(EXTRA_IDS).orEmpty()
        all = values.mapIndexed { index, value ->
            FacetValue(value, counts.getOrElse(index) { 0 }, ids.getOrElse(index) { 0 })
        }

        adapter = ValueAdapter(chosen, ::choose)
        views.list.layoutManager = LinearLayoutManager(this)
        views.list.adapter = adapter
        adapter.submit(all)

        views.filter.visibility = if (all.size > 12) View.VISIBLE else View.GONE
        views.filter.addTextChangedListener(object : TextWatcher {
            override fun afterTextChanged(s: Editable?) {
                val needle = s?.toString()?.trim().orEmpty()
                adapter.submit(
                    if (needle.isBlank()) all
                    else all.filter { it.value.contains(needle, ignoreCase = true) }
                )
            }

            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
        })

        views.anyValue.setOnClickListener { choose(null) }
    }

    /** Null means "any": the way back out of a filter without leaving the screen. */
    private fun choose(value: FacetValue?) {
        setResult(
            Activity.RESULT_OK,
            Intent()
                .putExtra(EXTRA_FACET, facet)
                .putExtra(EXTRA_CHOSEN, value?.value.orEmpty())
                .putExtra(EXTRA_ID, value?.id ?: 0),
        )
        finish()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()                    // Changed nothing: leave the filter as it was.
        return true
    }

    companion object {
        const val EXTRA_FACET = "facet"
        const val EXTRA_LABEL = "label"
        const val EXTRA_CHOSEN = "chosen"
        const val EXTRA_ID = "id"
        const val EXTRA_VALUES = "values"
        const val EXTRA_COUNTS = "counts"
        const val EXTRA_IDS = "ids"
    }
}

private class ValueAdapter(
    private val chosen: String,
    private val onClick: (FacetValue) -> Unit,
) : RecyclerView.Adapter<ValueAdapter.Holder>() {

    private val values = mutableListOf<FacetValue>()

    fun submit(items: List<FacetValue>) {
        values.clear()
        values.addAll(items)
        notifyDataSetChanged()
    }

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val value: TextView = view.findViewById(R.id.value)
        val count: TextView = view.findViewById(R.id.count)
        val tick: View = view.findViewById(R.id.tick)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = Holder(
        LayoutInflater.from(parent.context).inflate(R.layout.item_facet_value, parent, false)
    )

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val item = values[position]
        holder.value.text = item.value
        // Collections carry an id rather than a count, and "(0)" beside one
        // would read as an empty collection.
        holder.count.text = if (item.count > 0) item.count.toString() else ""
        holder.tick.visibility = if (item.value == chosen) View.VISIBLE else View.INVISIBLE
        holder.itemView.setOnClickListener { onClick(item) }
    }

    override fun getItemCount() = values.size
}
