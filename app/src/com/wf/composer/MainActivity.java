package com.wf.composer;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public class MainActivity extends Activity {
    private static final String USB_URL = "http://localhost:8741/";
    private static final String PAIR_PAGE = "file:///android_asset/pair.html";
    private static final int FILE_REQ = 42;
    private static final int PERM_REQ = 43;
    private WebView web;
    private SharedPreferences prefs;
    private ValueCallback<Uri[]> fileCallback;
    private List<String> candidates;
    private int idx;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getPreferences(MODE_PRIVATE);

        String extra = getIntent().getStringExtra("url");
        if (extra != null && !extra.isEmpty()) saveUrl(extra);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.clearCache(true);
        web.addJavascriptInterface(new Bridge(), "WF");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView v, int code, String desc, String url) {
                if (url != null && url.startsWith("file:")) return;
                tryNext();
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                if (url != null && url.startsWith("http") && !url.equals(USB_URL)) saveUrl(url);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> request.grant(request.getResources()));
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb,
                                             FileChooserParams params) {
                fileCallback = cb;
                Intent i = new Intent(Intent.ACTION_GET_CONTENT)
                        .addCategory(Intent.CATEGORY_OPENABLE)
                        .setType("*/*")
                        .putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"image/*", "video/*"});
                try {
                    startActivityForResult(i, FILE_REQ);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        setContentView(web);

        // CAMERA/RECORD_AUDIO are runtime perms — the WebView's RESOURCE_*
        // grant in onPermissionRequest only yields a stream if the app holds them
        if (checkSelfPermission(android.Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{
                    android.Manifest.permission.CAMERA,
                    android.Manifest.permission.RECORD_AUDIO}, PERM_REQ);
        }

        loadStart();
    }

    private void loadStart() {
        String key = prefs.getString("key", "");
        candidates = new ArrayList<>();
        candidates.add(USB_URL);
        for (String u : loadUrls()) {
            String n = norm(u);
            if (!candidates.contains(n)) candidates.add(n);
        }
        if (key.isEmpty()) {
            autoPair();   // usb bridge? phone's localhost:8741 IS the laptop — pair silently
        } else {
            idx = 0;
            web.loadUrl(candidates.get(0) + "?k=" + key);
        }
    }

    // zero-interaction pairing: if `wrap bridge` is up, /pair-info is reachable
    // on the phone's localhost — grab the key and go straight to the composer.
    private void autoPair() {
        new Thread(() -> {
            try {
                java.net.HttpURLConnection c = (java.net.HttpURLConnection)
                        new java.net.URL(USB_URL + "pair-info").openConnection();
                c.setConnectTimeout(1500); c.setReadTimeout(1500);
                java.io.BufferedReader r = new java.io.BufferedReader(
                        new java.io.InputStreamReader(c.getInputStream()));
                StringBuilder b = new StringBuilder();
                for (String l; (l = r.readLine()) != null; ) b.append(l);
                String body = b.toString();
                String k = body.replaceAll(".*\"key\"\\s*:\\s*\"([0-9a-f]+)\".*", "$1");
                if (!k.equals(body) && k.length() >= 32) {
                    final String key = k;
                    prefs.edit().putString("key", key).apply();
                    saveUrl(USB_URL);
                    web.post(() -> web.loadUrl(USB_URL + "?k=" + key));
                    return;
                }
            } catch (Exception ignored) {}
            web.post(() -> web.loadUrl(PAIR_PAGE));   // no bridge — manual fallback
        }).start();
    }

    private void tryNext() {
        idx++;
        if (idx < candidates.size()) {
            web.post(() -> web.loadUrl(candidates.get(idx) + "?k=" + prefs.getString("key", "")));
        } else {
            showOffline();
        }
    }

    class Bridge {
        @JavascriptInterface
        public void pairOk(String url, String key) {
            prefs.edit().putString("key", key).apply();
            saveUrl(url);
            web.post(() -> web.loadUrl(norm(url) + "?k=" + key));
        }

        @JavascriptInterface
        public void unpair() {
            prefs.edit().remove("key").apply();
            web.post(() -> web.loadUrl(PAIR_PAGE));
        }

        // wispr flow dictates into the focused field via its accessibility bubble
        @JavascriptInterface
        public boolean hasWispr() {
            return getPackageManager().getLaunchIntentForPackage("com.wispr.flowapp") != null;
        }

        // raise the soft keyboard on the webview — the wispr bubble expands with it
        @JavascriptInterface
        public void focusField() {
            runOnUiThread(() -> {
                web.requestFocus();
                android.view.inputmethod.InputMethodManager imm =
                        (android.view.inputmethod.InputMethodManager)
                                getSystemService(INPUT_METHOD_SERVICE);
                if (imm != null) imm.showSoftInput(web, 0);
            });
        }
    }

    private void showOffline() {
        web.loadData("<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                + "<body style='background:#fafaf9;color:#111;font-family:"
                + "sans-serif;text-align:center;padding:36vh 24px 0'>"
                + "<h2 style='font-weight:600'>Can&rsquo;t reach your laptop</h2>"
                + "</body></html>", "text/html", "utf-8");
        new AlertDialog.Builder(this)
                .setTitle("Connection failed")
                .setMessage("Re-pair with the laptop, or retry")
                .setPositiveButton("Re-pair", (d, w) -> web.loadUrl(PAIR_PAGE))
                .setNegativeButton("Retry", (d, w) -> loadStart())
                .show();
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == FILE_REQ) {
            Uri[] uris = (res == RESULT_OK && data != null)
                    ? WebChromeClient.FileChooserParams.parseResult(res, data) : null;
            if (fileCallback != null) {
                fileCallback.onReceiveValue(uris);
                fileCallback = null;
            }
            return;
        }
        super.onActivityResult(req, res, data);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String u = intent != null ? intent.getStringExtra("url") : null;
        if (u != null && !u.isEmpty()) {
            saveUrl(u);
            web.loadUrl(norm(u) + "?k=" + prefs.getString("key", ""));
        }
    }

    private String norm(String u) {
        u = u.trim();
        if (!u.startsWith("http")) u = "http://" + u;
        if (!u.endsWith("/")) u += "/";
        u = u.split("\\?")[0];
        return u;
    }

    private void saveUrl(String u) {
        Set<String> all = new LinkedHashSet<>();
        all.add(norm(u));
        all.addAll(loadUrls());
        prefs.edit().putString("urls", join(all)).apply();
    }

    private List<String> loadUrls() {
        List<String> out = new ArrayList<>();
        for (String u : prefs.getString("urls", "").split(";")) {
            if (!u.isEmpty()) out.add(u);
        }
        return out;
    }

    private String join(Set<String> s) {
        StringBuilder b = new StringBuilder();
        for (String u : s) b.append(u).append(';');
        return b.toString();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }
}
