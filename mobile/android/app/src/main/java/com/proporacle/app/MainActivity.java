package com.proporacle.app;

import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

/**
 * Capacitor shell: keep default {@link com.getcapacitor.BridgeActivity} / {@code BridgeWebViewClient} intact.
 * Hamburger visibility is controlled in web CSS (do not inject global hide rules here).
 */
public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(OtaBundlePlugin.class);
        super.onCreate(savedInstanceState);

        // Ensure fresh bundled assets are shown after sync/reinstall.
        // Some devices retain stale WebView cache and can render an older tickets page.
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView != null) {
            webView.clearCache(true);
            webView.clearHistory();
            webView.clearFormData();
            WebSettings settings = webView.getSettings();
            if (settings != null) {
                settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
            }
        }
    }
}
