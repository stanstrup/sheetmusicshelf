package org.sheetmusicshelf.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.view.MotionEvent
import android.view.View

/**
 * The ink layer over one page.
 *
 * Marks are held in 0..1 of the page box and only turned into pixels when
 * drawn, which is what lets the same mark land correctly on a phone, on a
 * tablet, and in the web reader. The page image is drawn by the ImageView
 * underneath; this view is transparent and knows only where that image sits.
 */
class AnnotationOverlay(context: Context) : View(context) {

    /** Where the page image actually sits inside this view, in pixels. */
    private var pageBox = RectF()

    private val strokes = mutableListOf<Stroke>()
    private var drawing: MutableList<Pair<Float, Float>>? = null

    var editing: Boolean = false
    var penColor: String = "#c0392b"
    var tool: String = "pen"

    /** Called when a stroke finishes, or after an undo -- i.e. when there is
     *  something new worth saving. */
    var onChanged: ((List<Stroke>) -> Unit)? = null

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }

    fun setPageBox(box: RectF) {
        pageBox = box
        invalidate()
    }

    fun load(existing: List<Stroke>) {
        strokes.clear()
        strokes.addAll(existing)
        invalidate()
    }

    fun current(): List<Stroke> = strokes.toList()

    fun undo() {
        if (strokes.isNotEmpty()) {
            strokes.removeAt(strokes.size - 1)
            invalidate()
            onChanged?.invoke(current())
        }
    }

    fun clear() {
        if (strokes.isNotEmpty()) {
            strokes.clear()
            invalidate()
            onChanged?.invoke(current())
        }
    }

    val isEmpty: Boolean get() = strokes.isEmpty()

    override fun onDraw(canvas: Canvas) {
        if (pageBox.width() <= 0f || pageBox.height() <= 0f) return
        for (stroke in strokes) draw(canvas, stroke)
        drawing?.let { points ->
            draw(canvas, Stroke(tool, penColor, penWidth(), points))
        }
    }

    private fun draw(canvas: Canvas, stroke: Stroke) {
        if (stroke.points.isEmpty()) return
        paint.color = parse(stroke.color)
        paint.strokeWidth = (stroke.width * pageBox.width()).coerceAtLeast(1.5f)
        // A highlighter is the same ink, laid down translucent and much wider,
        // so it reads as marking a passage rather than writing on it.
        paint.alpha = if (stroke.tool == "highlighter") 90 else 255
        if (stroke.tool == "highlighter") paint.strokeWidth *= 4f

        val path = Path()
        val (firstX, firstY) = stroke.points.first()
        path.moveTo(toPixelX(firstX), toPixelY(firstY))
        for (index in 1 until stroke.points.size) {
            val (x, y) = stroke.points[index]
            path.lineTo(toPixelX(x), toPixelY(y))
        }
        // A single tap is a dot, and a zero-length path draws nothing.
        if (stroke.points.size == 1) {
            canvas.drawPoint(toPixelX(firstX), toPixelY(firstY), paint.apply { style = Paint.Style.FILL })
            paint.style = Paint.Style.STROKE
            return
        }
        canvas.drawPath(path, paint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!editing || pageBox.width() <= 0f) return false
        // Off the page itself, let the pager have the gesture.
        if (event.action == MotionEvent.ACTION_DOWN && !pageBox.contains(event.x, event.y)) return false

        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                parent?.requestDisallowInterceptTouchEvent(true)
                drawing = mutableListOf(toPageX(event.x) to toPageY(event.y))
            }
            MotionEvent.ACTION_MOVE -> {
                val points = drawing ?: return false
                // Sample sparsely: a stroke is capped server-side, and a point
                // every pixel is detail nobody can see on a rendered page.
                val point = toPageX(event.x) to toPageY(event.y)
                val last = points.last()
                if (kotlin.math.abs(point.first - last.first) > 0.001f ||
                    kotlin.math.abs(point.second - last.second) > 0.001f
                ) {
                    points.add(point)
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                parent?.requestDisallowInterceptTouchEvent(false)
                val points = drawing
                drawing = null
                if (points != null && points.isNotEmpty()) {
                    strokes.add(Stroke(tool, penColor, penWidth(), points))
                    onChanged?.invoke(current())
                }
            }
        }
        invalidate()
        return true
    }

    private fun penWidth(): Float = if (tool == "highlighter") 0.006f else 0.004f

    private fun toPageX(x: Float) = ((x - pageBox.left) / pageBox.width()).coerceIn(0f, 1f)
    private fun toPageY(y: Float) = ((y - pageBox.top) / pageBox.height()).coerceIn(0f, 1f)
    private fun toPixelX(x: Float) = pageBox.left + x * pageBox.width()
    private fun toPixelY(y: Float) = pageBox.top + y * pageBox.height()

    private fun parse(color: String): Int = try {
        Color.parseColor(color)
    } catch (_: IllegalArgumentException) {
        Color.parseColor("#c0392b")
    }
}
