package org.sheetmusicshelf.app

import android.graphics.Bitmap
import android.graphics.RectF
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.ProgressBar
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.google.android.material.snackbar.Snackbar
import org.sheetmusicshelf.app.databinding.ActivityReaderBinding
import java.util.concurrent.Executors

/**
 * One piece, a page at a time, with an ink layer over each page.
 *
 * The screen is kept awake for as long as this is open: a reader that sleeps
 * halfway down a page is worse than no reader at all when both hands are busy.
 */
class ReaderActivity : AppCompatActivity() {

    private lateinit var views: ActivityReaderBinding
    private lateinit var api: Api
    private val work = Executors.newFixedThreadPool(2)
    private val save = Executors.newSingleThreadExecutor()

    private var pieceId = 0
    private var pageCount = 1
    private var editing = false
    private var layers = mutableMapOf<Int, List<Stroke>>()
    private lateinit var pages: PageAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        views = ActivityReaderBinding.inflate(layoutInflater)
        setContentView(views.root)
        setSupportActionBar(views.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        api = Api(this)
        pieceId = intent.getIntExtra(EXTRA_PIECE_ID, 0)
        pageCount = intent.getIntExtra(EXTRA_PAGES, 1).coerceAtLeast(1)
        title = intent.getStringExtra(EXTRA_TITLE) ?: getString(R.string.app_name)

        pages = PageAdapter(pieceId, pageCount, api, work, ::strokesFor, ::onStrokesChanged) { editing }
        views.pager.adapter = pages
        views.pager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) = showPosition(position)
        })
        showPosition(0)

        views.draw.setOnClickListener { toggleEditing() }
        views.undo.setOnClickListener { pages.overlayAt(views.pager.currentItem)?.undo() }
        views.clear.setOnClickListener { confirmClear() }
        views.highlighter.setOnClickListener { toggleTool() }

        loadAnnotations()
    }

    private fun showPosition(position: Int) {
        views.position.text = getString(R.string.page_of, position + 1, pageCount)
    }

    private fun toggleEditing() {
        editing = !editing
        views.draw.setImageResource(if (editing) R.drawable.ic_done else R.drawable.ic_draw)
        views.inkTools.visibility = if (editing) View.VISIBLE else View.GONE
        // Paging while drawing would turn every stroke into a swipe.
        views.pager.isUserInputEnabled = !editing
        pages.setEditing(editing)
        Snackbar.make(
            views.root,
            if (editing) R.string.drawing_on else R.string.drawing_off,
            Snackbar.LENGTH_SHORT,
        ).show()
    }

    private fun toggleTool() {
        val overlay = pages.overlayAt(views.pager.currentItem) ?: return
        val highlighting = overlay.tool != "highlighter"
        pages.setTool(if (highlighting) "highlighter" else "pen")
        views.highlighter.setImageResource(
            if (highlighting) R.drawable.ic_pen else R.drawable.ic_highlighter
        )
    }

    private fun confirmClear() {
        val page = views.pager.currentItem + 1
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.clear_page_title, page))
            .setMessage(R.string.clear_page_message)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.clear) { _, _ ->
                pages.overlayAt(views.pager.currentItem)?.clear()
            }
            .show()
    }

    private fun strokesFor(page: Int): List<Stroke> = layers[page].orEmpty()

    private fun onStrokesChanged(page: Int, strokes: List<Stroke>) {
        layers[page] = strokes
        // Saved as soon as a stroke finishes: this is a networked reader with
        // no local store, and a mark that only exists in memory is a mark that
        // is one backgrounded app away from being lost.
        save.execute {
            try {
                api.putAnnotations(pieceId, page, strokes)
            } catch (error: Exception) {
                runOnUiThread {
                    Snackbar.make(
                        views.root,
                        getString(R.string.mark_not_saved, error.message ?: ""),
                        Snackbar.LENGTH_LONG,
                    ).show()
                }
            }
        }
    }

    private fun loadAnnotations() {
        work.execute {
            try {
                val loaded = api.annotations(pieceId)
                runOnUiThread {
                    layers = loaded.toMutableMap()
                    pages.notifyDataSetChanged()
                }
            } catch (_: Exception) {
                // Marks are an addition to the page, not the point of it: a
                // failure here must not stop the music being readable.
            }
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    override fun onDestroy() {
        work.shutdownNow()
        save.shutdown()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_PIECE_ID = "piece_id"
        const val EXTRA_TITLE = "title"
        const val EXTRA_PAGES = "pages"
    }
}

/** One page per position: the image, with its ink layer on top. */
private class PageAdapter(
    private val pieceId: Int,
    private val pageCount: Int,
    private val api: Api,
    private val work: java.util.concurrent.ExecutorService,
    private val strokesFor: (Int) -> List<Stroke>,
    private val onChanged: (Int, List<Stroke>) -> Unit,
    private val editing: () -> Boolean,
) : RecyclerView.Adapter<PageAdapter.Holder>() {

    private val live = mutableMapOf<Int, Holder>()
    private var tool = "pen"

    class Holder(val frame: FrameLayout) : RecyclerView.ViewHolder(frame) {
        val image: ImageView = frame.findViewById(R.id.page)
        val progress: ProgressBar = frame.findViewById(R.id.progress)
        lateinit var overlay: AnnotationOverlay
    }

    fun overlayAt(position: Int): AnnotationOverlay? = live[position]?.overlay

    fun setEditing(on: Boolean) {
        live.values.forEach { it.overlay.editing = on }
    }

    fun setTool(name: String) {
        tool = name
        live.values.forEach { it.overlay.tool = name }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val frame = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_page, parent, false) as FrameLayout
        val holder = Holder(frame)
        holder.overlay = AnnotationOverlay(parent.context)
        frame.addView(
            holder.overlay,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        return holder
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val page = position + 1
        live[position] = holder
        holder.overlay.editing = editing()
        holder.overlay.tool = tool
        holder.overlay.load(strokesFor(page))
        holder.overlay.onChanged = { strokes -> onChanged(page, strokes) }
        holder.image.setImageDrawable(null)
        holder.progress.visibility = View.VISIBLE

        val width = holder.itemView.resources.displayMetrics.widthPixels.coerceAtLeast(800)
        work.execute {
            val bitmap = try {
                api.page(pieceId, page, width)
            } catch (_: Exception) {
                null
            }
            holder.itemView.post {
                holder.progress.visibility = View.GONE
                if (bitmap != null) {
                    holder.image.setImageBitmap(bitmap)
                    holder.image.post { holder.overlay.setPageBox(imageBox(holder.image, bitmap)) }
                }
            }
        }
    }

    /**
     * Where the page actually sits inside the ImageView.
     *
     * fitCenter letterboxes, so the image is not the view: ink placed against
     * the view's bounds would drift by the size of the margins.
     */
    private fun imageBox(view: ImageView, bitmap: Bitmap): RectF {
        val viewWidth = view.width.toFloat()
        val viewHeight = view.height.toFloat()
        if (viewWidth <= 0f || viewHeight <= 0f) return RectF(0f, 0f, 0f, 0f)
        val scale = minOf(viewWidth / bitmap.width, viewHeight / bitmap.height)
        val drawnWidth = bitmap.width * scale
        val drawnHeight = bitmap.height * scale
        val left = (viewWidth - drawnWidth) / 2f
        val top = (viewHeight - drawnHeight) / 2f
        return RectF(left, top, left + drawnWidth, top + drawnHeight)
    }

    override fun onViewRecycled(holder: Holder) {
        live.entries.removeAll { it.value === holder }
        super.onViewRecycled(holder)
    }

    override fun getItemCount() = pageCount
}
