package org.sheetmusicshelf.app

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.sheetmusicshelf.app.databinding.ActivityBrowseBinding
import java.util.concurrent.Executors

/** The catalogue: search, and tap a piece to read it. */
class BrowseActivity : AppCompatActivity() {

    private lateinit var views: ActivityBrowseBinding
    private lateinit var api: Api
    private val work = Executors.newSingleThreadExecutor()
    private val adapter = PieceAdapter { open(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        views = ActivityBrowseBinding.inflate(layoutInflater)
        setContentView(views.root)
        setSupportActionBar(views.toolbar)
        api = Api(this)

        views.list.layoutManager = LinearLayoutManager(this)
        views.list.adapter = adapter

        views.search.setOnEditorActionListener { _, _, _ ->
            load(views.search.text.toString()); true
        }
        views.retry.setOnClickListener { load(views.search.text.toString()) }
    }

    override fun onResume() {
        super.onResume()
        if (!Prefs(this).configured) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        load(views.search.text.toString())
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.browse, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == R.id.action_settings) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return true
        }
        return super.onOptionsItemSelected(item)
    }

    private fun load(query: String) {
        views.status.text = getString(R.string.loading)
        views.status.visibility = View.VISIBLE
        views.retry.visibility = View.GONE
        work.execute {
            try {
                val pieces = api.pieces(query)
                runOnUiThread {
                    adapter.submit(pieces)
                    views.status.visibility = if (pieces.isEmpty()) View.VISIBLE else View.GONE
                    views.status.text = getString(R.string.nothing_found)
                }
            } catch (error: Exception) {
                runOnUiThread {
                    adapter.submit(emptyList())
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
