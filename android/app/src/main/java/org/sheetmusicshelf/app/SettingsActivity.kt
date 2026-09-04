package org.sheetmusicshelf.app

import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import org.sheetmusicshelf.app.databinding.ActivitySettingsBinding
import java.util.concurrent.Executors

/** Where the server is, and the token that gets in. */
class SettingsActivity : AppCompatActivity() {

    private lateinit var views: ActivitySettingsBinding
    private val work = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        views = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(views.root)
        setSupportActionBar(views.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        val prefs = Prefs(this)
        views.server.setText(prefs.serverUrl)
        views.token.setText(prefs.token)

        views.save.setOnClickListener {
            prefs.serverUrl = views.server.text.toString()
            prefs.token = views.token.text.toString()
            views.server.setText(prefs.serverUrl)          // show what was stored
            test()
        }
    }

    /** Prove the address and the token together, rather than finding out on
     *  the browse screen with a blank list and no explanation. */
    private fun test() {
        views.result.visibility = View.VISIBLE
        views.result.text = getString(R.string.checking)
        work.execute {
            val message = try {
                Api(this).check()
            } catch (error: Api.NotConfigured) {
                getString(R.string.fill_both)
            } catch (error: Exception) {
                error.message ?: getString(R.string.could_not_reach)
            }
            runOnUiThread { views.result.text = message }
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    override fun onDestroy() {
        work.shutdownNow()
        super.onDestroy()
    }
}
