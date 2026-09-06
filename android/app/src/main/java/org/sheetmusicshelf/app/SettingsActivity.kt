package org.sheetmusicshelf.app

import android.content.Intent
import android.net.Uri
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

        views.versionText.text = getString(R.string.current_version, BuildConfig.VERSION_NAME)

        views.save.setOnClickListener {
            prefs.serverUrl = views.server.text.toString()
            prefs.token = views.token.text.toString()
            views.server.setText(prefs.serverUrl)          // show what was stored
            test()
        }

        views.checkUpdate.setOnClickListener { checkForUpdate() }
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

    private fun checkForUpdate() {
        views.updateResult.visibility = View.VISIBLE
        views.updateResult.text = getString(R.string.checking)
        views.updateResult.setOnClickListener(null)
        work.execute {
            val offered = try {
                Api(this).offeredVersion()
            } catch (_: Exception) {
                null
            }
            val mine = packageManager.getPackageInfo(packageName, 0).longVersionCode
            runOnUiThread {
                if (offered == null || offered.versionCode <= mine) {
                    views.updateResult.text = getString(R.string.up_to_date)
                } else {
                    views.updateResult.text =
                        getString(R.string.update_available_inline, offered.versionName)
                    views.updateResult.setOnClickListener {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(offered.url)))
                    }
                }
            }
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
