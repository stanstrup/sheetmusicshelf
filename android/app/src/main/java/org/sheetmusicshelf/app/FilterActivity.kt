package org.sheetmusicshelf.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.sheetmusicshelf.app.databinding.ActivityFilterBinding
import java.util.concurrent.Executors

/**
 * The sidebar the website has, as a screen.
 *
 * Each facet shows what it is currently narrowed to, so the whole state of the
 * browse is readable at a glance rather than being spread across chips. Values
 * are fetched once and handed to the picker, which keeps a tap from waiting on
 * the network.
 */
class FilterActivity : AppCompatActivity() {

    private lateinit var views: ActivityFilterBinding
    private val work = Executors.newSingleThreadExecutor()
    private var filters = Filters()
    private var facets: Map<String, List<FacetValue>> = emptyMap()
    private lateinit var adapter: FacetAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        views = ActivityFilterBinding.inflate(layoutInflater)
        setContentView(views.root)
        setSupportActionBar(views.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        filters = Filters.from(intent.extras)
        adapter = FacetAdapter(::pick)
        views.list.layoutManager = LinearLayoutManager(this)
        views.list.adapter = adapter
        redraw()

        views.clear.setOnClickListener {
            filters = filters.cleared()
            redraw()
        }
        views.apply.setOnClickListener { done() }
        load()
    }

    private fun load() {
        views.status.visibility = View.VISIBLE
        work.execute {
            try {
                val loaded = Api(this).facets()
                runOnUiThread {
                    facets = loaded
                    views.status.visibility = View.GONE
                    redraw()
                }
            } catch (error: Exception) {
                runOnUiThread {
                    views.status.text = error.message ?: getString(R.string.could_not_reach)
                }
            }
        }
    }

    private fun redraw() {
        adapter.submit(
            Filters.FACETS.map { facet ->
                FacetRow(
                    facet = facet,
                    label = Filters.label(facet),
                    chosen = chosenLabel(facet),
                    available = facets[facet]?.size ?: 0,
                )
            }
        )
        views.clear.isEnabled = !filters.isEmpty
    }

    private fun chosenLabel(facet: String): String {
        if (facet == Filters.COLLECTION) {
            if (filters.collectionId == 0) return ""
            return facets[facet]?.firstOrNull { it.id == filters.collectionId }?.value
                ?: getString(R.string.one_collection)
        }
        return filters.valueOf(facet)
    }

    private fun pick(row: FacetRow) {
        val values = facets[row.facet].orEmpty()
        if (values.isEmpty()) return
        val intent = Intent(this, FacetValuesActivity::class.java)
            .putExtra(FacetValuesActivity.EXTRA_FACET, row.facet)
            .putExtra(FacetValuesActivity.EXTRA_LABEL, row.label)
            .putExtra(FacetValuesActivity.EXTRA_CHOSEN, chosenLabel(row.facet))
            .putStringArrayListExtra(
                FacetValuesActivity.EXTRA_VALUES, ArrayList(values.map { it.value })
            )
            .putIntegerArrayListExtra(
                FacetValuesActivity.EXTRA_COUNTS, ArrayList(values.map { it.count })
            )
            .putIntegerArrayListExtra(
                FacetValuesActivity.EXTRA_IDS, ArrayList(values.map { it.id })
            )
        picker.launch(intent)
    }

    private val picker = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode != Activity.RESULT_OK) return@registerForActivityResult
        val data = result.data ?: return@registerForActivityResult
        val facet = data.getStringExtra(FacetValuesActivity.EXTRA_FACET) ?: return@registerForActivityResult
        val value = data.getStringExtra(FacetValuesActivity.EXTRA_CHOSEN).orEmpty()
        filters = if (facet == Filters.COLLECTION) {
            filters.copy(collectionId = data.getIntExtra(FacetValuesActivity.EXTRA_ID, 0))
        } else {
            filters.with(facet, value)
        }
        redraw()
    }

    private fun done() {
        setResult(Activity.RESULT_OK, Intent().putExtras(filters.into(Bundle())))
        finish()
    }

    override fun onSupportNavigateUp(): Boolean {
        done()                      // Backing out keeps the choices, like the web.
        return true
    }

    override fun onDestroy() {
        work.shutdownNow()
        super.onDestroy()
    }
}

data class FacetRow(val facet: String, val label: String, val chosen: String, val available: Int)

private class FacetAdapter(
    private val onClick: (FacetRow) -> Unit,
) : RecyclerView.Adapter<FacetAdapter.Holder>() {

    private val rows = mutableListOf<FacetRow>()

    fun submit(items: List<FacetRow>) {
        rows.clear()
        rows.addAll(items)
        notifyDataSetChanged()
    }

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val label: TextView = view.findViewById(R.id.label)
        val chosen: TextView = view.findViewById(R.id.chosen)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = Holder(
        LayoutInflater.from(parent.context).inflate(R.layout.item_facet, parent, false)
    )

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val row = rows[position]
        val context = holder.itemView.context
        holder.label.text = row.label
        holder.chosen.text = when {
            row.chosen.isNotBlank() -> row.chosen
            row.available > 0 -> context.getString(R.string.any_of, row.available)
            else -> context.getString(R.string.nothing_to_choose)
        }
        // A facet that is narrowing something should read as active.
        holder.chosen.alpha = if (row.chosen.isNotBlank()) 1f else 0.6f
        holder.itemView.isEnabled = row.available > 0
        holder.itemView.setOnClickListener { onClick(row) }
    }

    override fun getItemCount() = rows.size
}
