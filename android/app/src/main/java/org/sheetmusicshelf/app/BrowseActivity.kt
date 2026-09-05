package org.sheetmusicshelf.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.chip.Chip
import org.sheetmusicshelf.app.databinding.ActivityBrowseBinding
import java.util.concurrent.Executors

/**
 * The catalogue: narrow it, then tap a piece to read it.
 *
 * Search alone is only useful when you already know what you are looking for.
 * Most of the time you are looking *around* -- everything by Chopin, everything
 * for four hands -- which is what the facets are for. What is currently
 * narrowed shows as chips, so the state of the browse is always visible and
 * always one tap from being undone.
 */
class BrowseActivity : AppCompatActivity() {

    private lateinit var views: ActivityBrowseBinding
    private lateinit var api: Api
    private val work = Executors.newSingleThreadExecutor()
    private val adapter = PieceAdapter { open(it) }
    private var filters = Filters()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        views = ActivityBrowseBinding.inflate(layoutInflater)
        setContentView(views.root)
        setSupportActionBar(views.toolbar)
        api = Api(this)

        views.list.layoutManager = LinearLayoutManager(this)
        views.list.adapter = adapter

        views.search.setOnEditorActionListener { _, _, _ ->
            filters = filters.copy(query = views.search.text.toString())
            load(); true
        }
        views.retry.setOnClickListener { load() }
        views.filterButton.setOnClickListener {
            filterScreen.launch(
                Intent(this, FilterActivity::class.java).putExtras(filters.into(Bundle()))
            )
        }
    }

    private val filterScreen = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode != Activity.RESULT_OK) return@registerForActivityResult
        filters = Filters.from(result.data?.extras).copy(query = views.search.text.toString())
        load()
    }

    override fun onResume() {
        super.onResume()
        if (!Prefs(this).configured) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        load()
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.browse, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        R.id.action_settings -> {
            startActivity(Intent(this, SettingsActivity::class.java)); true
        }
        R.id.action_sort -> { chooseSort(); true }
        else -> super.onOptionsItemSelected(item)
    }

    private fun chooseSort() {
        val labels = Filters.SORTS.map { it.second }.toTypedArray()
        val current = Filters.SORTS.indexOfFirst { it.first == filters.sort }
        AlertDialog.Builder(this)
            .setTitle(R.string.sort_by)
            .setSingleChoiceItems(labels, current) { dialog, which ->
                filters = filters.copy(sort = Filters.SORTS[which].first)
                dialog.dismiss()
                load()
            }
            .show()
    }

    /** One chip per active facet; tapping one drops that filter. */
    private fun showChips() {
        views.chips.removeAllViews()
        val active = filters.active
        val hasCollection = filters.collectionId != 0
        views.chips.visibility = if (active.isEmpty() && !hasCollection) View.GONE else View.VISIBLE

        for ((facet, value) in active) {
            views.chips.addView(chip("${Filters.label(facet)}: $value") {
                filters = filters.without(facet)
                load()
            })
        }
        if (hasCollection) {
            views.chips.addView(chip(getString(R.string.one_collection)) {
                filters = filters.copy(collectionId = 0)
                load()
            })
        }
        if (active.size + (if (hasCollection) 1 else 0) > 1) {
            views.chips.addView(chip(getString(R.string.clear_all)) {
                filters = filters.cleared()
                views.search.setText("")
                load()
            })
        }
    }

    private fun chip(text: String, onClose: () -> Unit): Chip = Chip(this).apply {
        this.text = text
        isCloseIconVisible = true
        setOnCloseIconClickListener { onClose() }
        setOnClickListener { onClose() }
    }

    private fun load() {
        showChips()
        views.status.text = getString(R.string.loading)
        views.status.visibility = View.VISIBLE
        views.retry.visibility = View.GONE
        val asked = filters
        work.execute {
            try {
                val pieces = api.pieces(asked)
                runOnUiThread {
                    if (asked != filters) return@runOnUiThread   // a newer request won
                    adapter.submit(pieces)
                    views.list.scrollToPosition(0)
                    views.status.visibility = if (pieces.isEmpty()) View.VISIBLE else View.GONE
                    views.status.text = getString(R.string.nothing_found)
                    views.count.text = resources.getQuantityString(
                        R.plurals.pieces_found, pieces.size, pieces.size
                    )
                    views.count.visibility = if (pieces.isEmpty()) View.GONE else View.VISIBLE
                }
            } catch (error: Exception) {
                runOnUiThread {
                    adapter.submit(emptyList())
                    views.count.visibility = View.GONE
                    views.status.visibility = View.VISIBLE
                    views.status.text = error.message ?: getString(R.string.could_not_reach)
                    views.retry.visibility = View.VISIBLE
                }
            }
        }
    }

    private fun open(piece: Piece) {
        startActivity(
            Intent(this, ReaderActivity::class.java)
                .putExtra(ReaderActivity.EXTRA_PIECE_ID, piece.id)
                .putExtra(ReaderActivity.EXTRA_TITLE, piece.title)
                .putExtra(ReaderActivity.EXTRA_PAGES, piece.pageCount)
        )
    }

    override fun onDestroy() {
        work.shutdownNow()
        super.onDestroy()
    }
}

private class PieceAdapter(
    private val onClick: (Piece) -> Unit,
) : RecyclerView.Adapter<PieceAdapter.Holder>() {

    private val pieces = mutableListOf<Piece>()

    fun submit(items: List<Piece>) {
        pieces.clear()
        pieces.addAll(items)
        notifyDataSetChanged()
    }

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val title: TextView = view.findViewById(R.id.title)
        val subtitle: TextView = view.findViewById(R.id.subtitle)
        val pages: TextView = view.findViewById(R.id.pages)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = Holder(
        LayoutInflater.from(parent.context).inflate(R.layout.item_piece, parent, false)
    )

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val piece = pieces[position]
        holder.title.text = piece.title
        holder.subtitle.text = piece.subtitle
        holder.subtitle.visibility = if (piece.subtitle.isBlank()) View.GONE else View.VISIBLE
        holder.pages.text = holder.itemView.context.resources
            .getQuantityString(R.plurals.pages, piece.pageCount, piece.pageCount)
        holder.itemView.setOnClickListener { onClick(piece) }
    }

    override fun getItemCount() = pieces.size
}
