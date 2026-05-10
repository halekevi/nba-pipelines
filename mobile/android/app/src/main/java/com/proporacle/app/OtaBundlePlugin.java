package com.proporacle.app;

import android.app.Activity;
import android.content.SharedPreferences;
import com.getcapacitor.JSObject;
import com.getcapacitor.Logger;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * DIY OTA: download {@code bundle.zip} from Railway, unzip to files/ota-www, then
 * {@link com.getcapacitor.Bridge#setServerBasePath} + persist (same prefs as {@code WebView} plugin).
 */
@CapacitorPlugin(name = "OtaBundle")
public class OtaBundlePlugin extends Plugin {

    private static final String TAG = "OtaBundle";
    private static final String OTA_DIR = "ota-www";
    private static final String TMP_ZIP = "ota-download.zip";

    private void persistServerPath(String path) {
        SharedPreferences prefs =
            getContext().getSharedPreferences(com.getcapacitor.plugin.WebView.WEBVIEW_PREFS_NAME, Activity.MODE_PRIVATE);
        prefs.edit().putString(com.getcapacitor.plugin.WebView.CAP_SERVER_PATH, path).apply();
    }

    private static void deleteRecursive(File f) {
        if (f.isDirectory()) {
            File[] kids = f.listFiles();
            if (kids != null) {
                for (File c : kids) {
                    deleteRecursive(c);
                }
            }
        }
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }

    private static void downloadToFile(String urlStr, File dest) throws IOException {
        HttpURLConnection conn = null;
        InputStream in = null;
        FileOutputStream out = null;
        try {
            URL url = new URL(urlStr);
            conn = (HttpURLConnection) url.openConnection();
            conn.setInstanceFollowRedirects(true);
            conn.setConnectTimeout(30_000);
            conn.setReadTimeout(120_000);
            conn.setRequestMethod("GET");
            conn.connect();
            int code = conn.getResponseCode();
            if (code < 200 || code >= 300) {
                throw new IOException("HTTP " + code);
            }
            in = new BufferedInputStream(conn.getInputStream());
            out = new FileOutputStream(dest);
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            out.flush();
        } finally {
            if (in != null) {
                try {
                    in.close();
                } catch (IOException ignored) {
                }
            }
            if (out != null) {
                try {
                    out.close();
                } catch (IOException ignored) {
                }
            }
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static void unzipToDir(File zipFile, File targetDir) throws IOException {
        targetDir.mkdirs();
        try (ZipInputStream zis = new ZipInputStream(new BufferedInputStream(new FileInputStream(zipFile)))) {
            ZipEntry entry;
            byte[] buffer = new byte[8192];
            String canonBase = targetDir.getCanonicalPath();
            while ((entry = zis.getNextEntry()) != null) {
                if (entry.isDirectory()) {
                    zis.closeEntry();
                    continue;
                }
                String name = entry.getName();
                if (name.contains("..")) {
                    zis.closeEntry();
                    continue;
                }
                File outFile = new File(targetDir, name);
                String canon = outFile.getCanonicalPath();
                if (!canon.startsWith(canonBase + File.separator) && !canon.equals(canonBase)) {
                    zis.closeEntry();
                    continue;
                }
                File parent = outFile.getParentFile();
                if (parent != null) {
                    parent.mkdirs();
                }
                try (FileOutputStream fos = new FileOutputStream(outFile)) {
                    int len;
                    while ((len = zis.read(buffer)) > 0) {
                        fos.write(buffer, 0, len);
                    }
                }
                zis.closeEntry();
            }
        }
    }

    /**
     * After a new APK install, Capacitor clears {@code serverBasePath} prefs but internal storage may
     * still contain a previous {@code ota-www}. Re-point the WebView at that folder if needed.
     */
    @PluginMethod
    public void reapplyIfPresent(PluginCall call) {
        File ota = new File(getContext().getFilesDir(), OTA_DIR);
        File index = new File(ota, "index.html");
        if (!index.isFile()) {
            call.resolve(new JSObject().put("reapplied", false));
            return;
        }
        SharedPreferences prefs =
            getContext().getSharedPreferences(com.getcapacitor.plugin.WebView.WEBVIEW_PREFS_NAME, Activity.MODE_PRIVATE);
        String p = prefs.getString(com.getcapacitor.plugin.WebView.CAP_SERVER_PATH, "");
        if (p != null && !p.isEmpty()) {
            File pd = new File(p);
            if (pd.isDirectory() && new File(pd, "index.html").isFile()) {
                call.resolve(new JSObject().put("reapplied", false));
                return;
            }
        }
        String path = ota.getAbsolutePath();
        getBridge().setServerBasePath(path);
        persistServerPath(path);
        call.resolve(new JSObject().put("reapplied", true));
    }

    @PluginMethod
    public void applyBundleFromUrl(PluginCall call) {
        String urlStr = call.getString("url");
        if (urlStr == null || urlStr.isEmpty()) {
            call.reject("missing_url");
            return;
        }
        ExecutorService ex = Executors.newSingleThreadExecutor();
        ex.execute(
            () -> {
                try {
                    File base = getContext().getFilesDir();
                    File zipOut = new File(base, TMP_ZIP);
                    File ota = new File(base, OTA_DIR);
                    downloadToFile(urlStr, zipOut);
                    deleteRecursive(ota);
                    unzipToDir(zipOut, ota);
                    //noinspection ResultOfMethodCallIgnored
                    zipOut.delete();
                    if (!new File(ota, "index.html").isFile()) {
                        throw new IOException("bundle_missing_index");
                    }
                    String path = ota.getAbsolutePath();
                    getBridge()
                        .executeOnMainThread(
                            () -> {
                                try {
                                    getBridge().setServerBasePath(path);
                                    persistServerPath(path);
                                    call.resolve(new JSObject().put("ok", true).put("path", path));
                                } catch (Exception e) {
                                    Logger.error(TAG, "applyBundleFromUrl finalize failed", e);
                                    call.reject("ota_finalize_failed", e);
                                }
                            }
                        );
                } catch (Exception e) {
                    Logger.error(TAG, "applyBundleFromUrl failed", e);
                    getBridge().executeOnMainThread(() -> call.reject("ota_failed", e));
                }
            }
        );
    }
}
