package cn.contractreview.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ContentValues;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

/**
 * 合同审查 App —— WebView 壳。
 * 加载后台 H5（默认互联网地址，可在 App 内长按改成局域网或其它映射地址）。
 *
 * 除容器与地址配置外，壳只补两件 WebView 自己干不了的事：
 *   1. 选文件上传 —— onShowFileChooser（否则 <input type="file"> 点了没反应）
 *   2. 下载文件   —— CRApp.download() 桥：带会话 Cookie 拉流、存盘、交给系统打开
 *                   （WebView 不渲染 PDF；blob:/<a download> 也不会进 DownloadListener）
 *
 * 适配纯血鸿蒙「卓易通」容器：允许明文 HTTP、返回键回退网页、不申请任何存储权限
 * （写应用外部私有目录；API 29+ 再镜像一份到「下载/合同审查」）。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "cr_prefs";
    private static final String KEY_URL = "base_url";
    // 默认后台地址来自 android/assets/backend-url.txt；出包前按环境替换。
    // 这样 APK 里不再硬编码某台机器的 IP/域名。
    private String defaultUrl = "http://10.0.2.2:8790/";   // Android 模拟器 -> 宿主机
    private final String legacyLanUrl = "";                // 旧版迁移地址留空即关闭迁移
    private static final int REQ_PICK_FILE = 1001;
    private static final String UA_TAG = "ContractReviewApp/1.5";

    private WebView web;
    private ProgressBar progress;
    private FrameLayout root;
    private String baseUrl = "";
    private boolean zh;

    private ValueCallback<Uri[]> pendingFileCallback;
    private final Handler ui = new Handler(Looper.getMainLooper());

    // 长按换地址：不能直接 setOnLongClickListener —— 那会在 WebView 之前吃掉长按，
    // 导致 H5 输入框里长按不出「粘贴」菜单。这里改成自己计时，且永不消费事件。
    private static final int LONG_PRESS_MS = 700;
    private float downX, downY;
    private int touchSlop;
    private boolean longPressFired;
    private final Runnable longPressTask = new Runnable() {
        @Override public void run() { onLongPressTimeout(); }
    };

    // 同一次点击可能既走 JS 桥又走导航拦截，这里做 1.2s 去重，避免同一份文件下两次。
    private String lastDlUrl = null;
    private long lastDlAt = 0L;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        zh = Locale.getDefault().getLanguage().toLowerCase(Locale.ROOT).startsWith("zh");
        defaultUrl = readAssetUrl();

        Window w = getWindow();
        w.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= 21) {
            w.setStatusBarColor(Color.parseColor("#2f6fed"));
            w.setNavigationBarColor(Color.parseColor("#ffffff"));
        }

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#ffffff"));
        // Android 15(targetSdk 35) 强制 edge-to-edge：网页一直画到系统手势条底下，
        // 而 WebView 算出来的 env(safe-area-inset-bottom) 是 0 —— 底部菜单就被那根
        // 手势胶囊压住、标签被裁掉一半（实测 innerH=满屏、navBottom=innerH）。
        // 这里按导航栏/状态栏 inset 给容器垫底和垫顶：
        //  - 底部：网页落在手势条上方，底部菜单不被胶囊压住；
        //  - 顶部：WebView 不再顶到状态栏底下，否则顶栏「EN」语言按钮被状态栏盖住点不中
        //    （WebView 里 env(safe-area-inset-top) 恒为 0，只能靠原生容器垫）。
        root.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @Override public WindowInsets onApplyWindowInsets(View v, WindowInsets insets) {
                int top = 0, bottom = 0;
                try {
                    if (Build.VERSION.SDK_INT >= 30) {
                        top = insets.getInsets(WindowInsets.Type.statusBars()).top;
                        // 手势条 / 三键导航
                        int nav = insets.getInsets(WindowInsets.Type.navigationBars()).bottom;
                        // 键盘：edge-to-edge 下 adjustResize 不再缩窗口，IME 也走 inset。
                        // 取两者最大值 —— 相当于 Flutter Scaffold 的 resizeToAvoidBottomInset，
                        // body 顶上去、底部菜单仍压在键盘上沿（不会整条被盖掉）。
                        int ime = insets.getInsets(WindowInsets.Type.ime()).bottom;
                        bottom = Math.max(nav, ime);
                    } else {
                        top = insets.getSystemWindowInsetTop();
                        bottom = insets.getSystemWindowInsetBottom();
                    }
                } catch (Exception ignored) {}
                if (v.getPaddingTop() != top || v.getPaddingBottom() != bottom) {
                    v.setPadding(v.getPaddingLeft(), top, v.getPaddingRight(), bottom);
                }
                return insets;          // 不消费，继续往下传
            }
        });
        setContentView(root);

        web = new WebView(this);
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);   // 始终拿最新 H5
        if (Build.VERSION.SDK_INT >= 21) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW); // 局域网 http
        }
        if (Build.VERSION.SDK_INT >= 19) {
            WebView.setWebContentsDebuggingEnabled(true);
        }
        s.setUserAgentString(s.getUserAgentString() + " " + UA_TAG);

        web.addJavascriptInterface(new Bridge(), "CRApp");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleUrl(url);
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (request == null || !request.isForMainFrame()) return false;
                return handleUrl(request.getUrl().toString());
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                progress.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
            }

            // 旧签名在主框架加载失败时仍会被回调（兼容至 target 35）。
            // 只在主页面本身失败时才弹地址设置，否则下载链接出错也会误弹。
            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                progress.setVisibility(View.GONE);
                if (failingUrl != null && baseUrl != null && !baseUrl.isEmpty()
                        && samePage(failingUrl, baseUrl)) {
                    showUrlDialog(true);
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setProgress(newProgress);
                progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
            }

            // —— 上传 PDF 的关键：没有它 <input type="file"> 点了毫无反应 ——
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (pendingFileCallback != null) {
                    pendingFileCallback.onReceiveValue(null);
                    pendingFileCallback = null;
                }
                pendingFileCallback = callback;

                Intent intent = null;
                try { intent = params != null ? params.createIntent() : null; } catch (Exception ignored) {}
                if (intent == null) {
                    intent = new Intent(Intent.ACTION_GET_CONTENT);
                    intent.setType("application/pdf");
                }
                intent.addCategory(Intent.CATEGORY_OPENABLE);

                try {
                    startActivityForResult(
                            Intent.createChooser(intent, zh ? "选择合同 PDF" : "Choose a PDF"),
                            REQ_PICK_FILE);
                    return true;
                } catch (Exception e) {
                    pendingFileCallback = null;
                    toast(zh ? "没有可用的文件选择器" : "No file picker available");
                    return false;
                }
            }
        });

        // 长按空白处 -> 修改后台地址（云端地址切换入口）。
        // 关键：返回 false 不消费触摸，WebView 照常处理文本选择/滚动；
        // 到点后先问页面「手指下面是输入框吗」，是的话什么都不做，把长按还给系统粘贴菜单。
        touchSlop = ViewConfiguration.get(this).getScaledTouchSlop();
        web.setOnTouchListener(new View.OnTouchListener() {
            @Override public boolean onTouch(View v, MotionEvent e) {
                switch (e.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        downX = e.getX();
                        downY = e.getY();
                        longPressFired = false;
                        ui.removeCallbacks(longPressTask);
                        ui.postDelayed(longPressTask, LONG_PRESS_MS);
                        break;
                    case MotionEvent.ACTION_MOVE:
                        if (Math.abs(e.getX() - downX) > touchSlop
                                || Math.abs(e.getY() - downY) > touchSlop) {
                            ui.removeCallbacks(longPressTask);
                        }
                        break;
                    case MotionEvent.ACTION_UP:
                    case MotionEvent.ACTION_CANCEL:
                        ui.removeCallbacks(longPressTask);
                        break;
                    default:
                        break;
                }
                return false;
            }
        });

        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        FrameLayout.LayoutParams pp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3));
        root.addView(progress, pp);

        baseUrl = loadUrl();
        if (baseUrl == null || baseUrl.isEmpty()) {
            showUrlDialog(true);                 // 首次启动：必须先设地址
        } else {
            web.loadUrl(baseUrl);
        }
    }

    /* ================= 长按换地址（与页面手势共存） ================= */

    /**
     * 判断手指落点是不是落在可编辑元素上。是的话本次长按交给 WebView/系统，
     * 用户能正常长按出「粘贴 / 全选」菜单；否则才弹后台地址设置。
     */
    private void onLongPressTimeout() {
        if (isFinishing() || web == null) return;
        final float density = getResources().getDisplayMetrics().density;
        final int x = Math.round(downX / density);
        final int y = Math.round(downY / density);

        String js = "(function(x,y){try{"
                + "var el=document.elementFromPoint(x,y);"
                + "while(el){var t=(el.tagName||'').toUpperCase();"
                + "if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return 'edit';"
                + "if(el.isContentEditable===true)return 'edit';"
                + "el=el.parentElement;}"
                + "var s=(window.getSelection&&window.getSelection());"
                + "if(s&&String(s).length>0)return 'select';"
                + "return 'plain';}catch(e){return 'plain';}})(" + x + "," + y + ")";

        try {
            web.evaluateJavascript(js, new ValueCallback<String>() {
                @Override public void onReceiveValue(String value) {
                    // value 形如 "plain" / "edit" / "select"；null 表示取不到（按空白处理）
                    if (value != null && (value.contains("edit") || value.contains("select"))) return;
                    if (longPressFired) return;
                    longPressFired = true;
                    showUrlDialog(false);
                }
            });
        } catch (Exception e) {
            showUrlDialog(false);
        }
    }

    /* ================= URL 分流 ================= */

    private boolean samePage(String a, String b) {
        String x = a.endsWith("/") ? a : a + "/";
        String y = b.endsWith("/") ? b : b + "/";
        return x.equalsIgnoreCase(y);
    }

    /** 站内下载接口：交给原生拉流（WebView 打开 PDF 只会白屏）。 */
    private boolean isDownloadUrl(String url) {
        if (url == null) return false;
        return url.matches(".*/api/contracts/[^/?#]+/(original|report|export)(\\?.*)?$");
    }

    private boolean handleUrl(String url) {
        if (url == null) return false;
        if (isDownloadUrl(url)) {
            startDownload(url, null);
            return true;
        }
        if (baseUrl != null && url.startsWith(baseUrl)) return false;
        // 外部链接交给系统浏览器
        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); }
        catch (Exception ignored) {}
        return true;
    }

    /* ================= 下载（JS 桥） ================= */

    public class Bridge {
        @JavascriptInterface
        public String platform() { return "android"; }

        @JavascriptInterface
        public void toast(final String msg) { MainActivity.this.toast(msg); }

        @JavascriptInterface
        public void download(final String url, final String fallbackName) {
            startDownload(url, fallbackName);
        }

        /** 页面内显式入口（登录页「服务器地址」），比长按更好发现。 */
        @JavascriptInterface
        public void serverConfig() {
            ui.post(new Runnable() {
                @Override public void run() { showUrlDialog(false); }
            });
        }
    }

    /** 同站判断：比协议+主机+端口+路径前缀，别用裸 startsWith（换云端地址/带子路径时会误判）。 */
    private boolean withinBackend(String url) {
        if (baseUrl == null || baseUrl.isEmpty() || url == null) return false;
        try {
            java.net.URI b = new java.net.URI(baseUrl);
            java.net.URI u = new java.net.URI(url);
            String us = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
            if (!us.equals("http") && !us.equals("https")) return false;
            if (b.getHost() == null || u.getHost() == null) return false;
            if (!b.getHost().equalsIgnoreCase(u.getHost())) return false;
            if (effPort(b) != effPort(u)) return false;
            String bp = b.getPath() == null || b.getPath().isEmpty() ? "/" : b.getPath();
            String up = u.getPath() == null || u.getPath().isEmpty() ? "/" : u.getPath();
            return up.startsWith(bp);
        } catch (Exception e) {
            return false;
        }
    }

    private static int effPort(java.net.URI u) {
        if (u.getPort() > 0) return u.getPort();
        return "https".equalsIgnoreCase(u.getScheme()) ? 443 : 80;
    }

    private void startDownload(final String url, final String fallbackName) {
        if (url == null || url.isEmpty()) return;
        // 页面来自自家后台，下载地址也必须限定在后台域内，避免被注入的脚本借壳下载任意内容。
        if (!withinBackend(url)) {
            toast(zh ? "下载地址不在后台范围内" : "Download URL outside backend");
            return;
        }
        final long now = System.currentTimeMillis();
        if (url.equals(lastDlUrl) && now - lastDlAt < 1200) return;   // 去重
        lastDlUrl = url;
        lastDlAt = now;

        toast(zh ? "开始下载…" : "Downloading…");
        new Thread(new Runnable() {
            @Override public void run() { fetchToFile(url, fallbackName); }
        }, "cr-download").start();
    }

    private void fetchToFile(String url, String fallbackName) {
        HttpURLConnection conn = null;
        InputStream in = null;
        OutputStream pub = null;
        Uri pubUri = null;
        File target = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(180000);
            conn.setInstanceFollowRedirects(true);
            conn.setRequestProperty("User-Agent", UA_TAG);
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null && !cookie.isEmpty()) conn.setRequestProperty("Cookie", cookie);

            int code = conn.getResponseCode();
            if (code != 200) {
                final String msg = zh ? ("下载失败 HTTP " + code) : ("Download failed HTTP " + code);
                ui.post(new Runnable() { @Override public void run() { toast(msg); } });
                return;
            }

            String ctype = conn.getContentType();
            String name = parseFilename(conn.getHeaderField("Content-Disposition"));
            if (name == null || name.trim().isEmpty()) name = fallbackName;
            String mime = guessMime(name, ctype);
            name = sanitize(name, mime);
            target = uniqueFile(FileProviderLite.baseDir(this), name);

            // API 29+ 无需权限镜像一份到「下载/合同审查」，用户能在文件管理器里找到。
            if (Build.VERSION.SDK_INT >= 29) {
                try {
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.MediaColumns.DISPLAY_NAME, target.getName());
                    cv.put(MediaStore.MediaColumns.MIME_TYPE, mime);
                    cv.put(MediaStore.MediaColumns.RELATIVE_PATH,
                            Environment.DIRECTORY_DOWNLOADS + "/合同审查");
                    Uri ins = getContentResolver()
                            .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                    if (ins != null) {
                        pubUri = ins;
                        pub = getContentResolver().openOutputStream(ins);
                    }
                } catch (Exception ignored) { pub = null; pubUri = null; }
            }

            in = conn.getInputStream();
            FileOutputStream fos = new FileOutputStream(target);
            try {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) {
                    fos.write(buf, 0, n);
                    if (pub != null) {
                        try { pub.write(buf, 0, n); }
                        catch (Exception e) { pub = null; pubUri = null; }   // 镜像失败就别对外声称存到「下载」
                    }
                }
                fos.flush();
            } finally {
                try { fos.close(); } catch (IOException ignored) {}
            }

            final File saved = target;
            final Uri savedPub = pubUri;
            final String savedMime = mime;
            ui.post(new Runnable() {
                @Override public void run() { showSavedDialog(saved, savedPub, savedMime); }
            });
        } catch (final Exception e) {
            // 半截文件不留：删本地临时文件 + 撤掉 MediaStore 里的空壳条目
            if (target != null && target.exists() && !target.delete()) { /* ignore */ }
            if (pubUri != null) {
                try { getContentResolver().delete(pubUri, null, null); } catch (Exception ignored) {}
            }
            final String msg = (zh ? "下载失败：" : "Download failed: ") + e.getMessage();
            ui.post(new Runnable() { @Override public void run() { toast(msg); } });
        } finally {
            if (in != null) try { in.close(); } catch (IOException ignored) {}
            if (pub != null) try { pub.close(); } catch (IOException ignored) {}
            if (conn != null) conn.disconnect();
        }
    }

    private void showSavedDialog(final File f, final Uri pubUri, final String mime) {
        if (isFinishing()) return;
        final String where = pubUri != null
                ? (zh ? "已保存到 下载/合同审查（文件管理器可查）" : "Saved to Download/合同审查")
                : (zh ? "已保存到应用私有目录" : "Saved to app storage");

        new AlertDialog.Builder(this)
            .setTitle(zh ? "下载完成" : "Download complete")
            .setMessage(f.getName() + "\n" + where)
            .setPositiveButton(zh ? "打开" : "Open", new DialogInterface.OnClickListener() {
                @Override public void onClick(DialogInterface d, int which) { openFile(f, mime); }
            })
            .setNeutralButton(zh ? "分享" : "Share", new DialogInterface.OnClickListener() {
                @Override public void onClick(DialogInterface d, int which) { shareFile(f, mime); }
            })
            .setNegativeButton(zh ? "关闭" : "Close", null)
            .show();
    }

    private void openFile(File f, String mime) {
        Uri u = FileProviderLite.uriFor(this, f);
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(u, mime);
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        // 部分阅读器只认 ClipData 里的 grant，两个都给上更稳。
        i.setClipData(ClipData.newRawUri(f.getName(), u));
        try {
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            toast(zh ? "没有能打开该文件的应用，试试「分享」" : "No app to open this file, try Share");
            shareFile(f, mime);
        } catch (Exception e) {
            toast((zh ? "打开失败：" : "Open failed: ") + e.getMessage());
        }
    }

    private void shareFile(File f, String mime) {
        Uri u = FileProviderLite.uriFor(this, f);
        Intent i = new Intent(Intent.ACTION_SEND);
        i.setType(mime);
        i.putExtra(Intent.EXTRA_STREAM, u);
        i.setClipData(ClipData.newRawUri(f.getName(), u));
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(Intent.createChooser(i, zh ? "分享到" : "Share to"));
        } catch (Exception e) {
            toast(zh ? "分享失败" : "Share failed");
        }
    }

    /* ================= 文件名 / MIME 工具 ================= */

    /** Content-Disposition 取值：优先 RFC 5987 的 filename*，其次 filename。 */
    private static String parseFilename(String cd) {
        if (cd == null) return null;
        String lower = cd.toLowerCase(Locale.ROOT);
        int star = lower.indexOf("filename*=");
        if (star >= 0) {
            String v = cd.substring(star + 10).trim();
            int semi = v.indexOf(';');
            if (semi >= 0) v = v.substring(0, semi);
            v = stripQuotes(v.trim());
            int q = v.indexOf("''");
            if (q >= 0) v = v.substring(q + 2);
            try { return java.net.URLDecoder.decode(v, "UTF-8"); } catch (Exception ignored) {}
        }
        int p = lower.indexOf("filename=");
        if (p >= 0) {
            String v = cd.substring(p + 9).trim();
            int semi = v.indexOf(';');
            if (semi >= 0) v = v.substring(0, semi);
            return stripQuotes(v.trim());
        }
        return null;
    }

    private static String stripQuotes(String v) {
        if (v.length() >= 2 && v.startsWith("\"") && v.endsWith("\"")) return v.substring(1, v.length() - 1);
        return v;
    }

    private static String sanitize(String name, String mime) {
        if (name == null || name.trim().isEmpty()) name = "download";
        name = name.replaceAll("[\\\\/:*?\"<>|\\r\\n\\t]", "_").trim();
        if (name.length() > 110) {
            int dot = name.lastIndexOf('.');
            String ext = dot > 0 ? name.substring(dot) : "";
            name = name.substring(0, 100) + ext;
        }
        if (!name.contains(".")) {
            name = name + (mime != null && mime.startsWith("text/csv") ? ".csv"
                    : mime != null && mime.startsWith("application/pdf") ? ".pdf" : ".bin");
        }
        return name;
    }

    private static String guessMime(String name, String serverType) {
        String n = name == null ? "" : name.toLowerCase(Locale.ROOT);
        if (n.endsWith(".pdf")) return "application/pdf";
        if (n.endsWith(".csv")) return "text/csv";
        if (n.endsWith(".zip")) return "application/zip";
        if (serverType != null && !serverType.isEmpty()) return serverType.split(";")[0].trim();
        return "*/*";
    }

    static String mimeOf(String name) { return guessMime(name, null); }

    private static File uniqueFile(File dir, String name) {
        File f = new File(dir, name);
        if (!f.exists()) return f;
        int dot = name.lastIndexOf('.');
        String stem = dot > 0 ? name.substring(0, dot) : name;
        String ext = dot > 0 ? name.substring(dot) : "";
        for (int i = 1; i < 500; i++) {
            File c = new File(dir, stem + "(" + i + ")" + ext);
            if (!c.exists()) return c;
        }
        return new File(dir, System.currentTimeMillis() + ext);
    }

    private void toast(final String msg) {
        ui.post(new Runnable() {
            @Override public void run() {
                Toast.makeText(MainActivity.this, msg, Toast.LENGTH_SHORT).show();
            }
        });
    }

    /* ================= 选择文件回调 ================= */

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_PICK_FILE) {
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null) {
                ClipData clip = data.getClipData();
                if (clip != null && clip.getItemCount() > 0) {
                    result = new Uri[clip.getItemCount()];
                    for (int i = 0; i < clip.getItemCount(); i++) {
                        result[i] = clip.getItemAt(i).getUri();
                    }
                } else if (data.getData() != null) {
                    result = new Uri[]{ data.getData() };
                }
            }
            if (pendingFileCallback != null) {
                pendingFileCallback.onReceiveValue(result);   // null = 用户取消，必须回传否则输入框永久卡死
                pendingFileCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    /* ================= 页面外壳 ================= */

    /** Read the build-time backend URL from assets; fall back to the emulator host. */
    private String readAssetUrl() {
        try {
            java.io.InputStream in = getAssets().open("backend-url.txt");
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[1024];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            in.close();
            String s = bos.toString("UTF-8").trim();
            if (!s.isEmpty()) return s;
        } catch (Exception ignored) {
            // Missing asset (older APK): keep the emulator default.
        }
        return "http://10.0.2.2:8790/";
    }

    private String loadUrl() {
        String s = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, "");
        s = (s == null) ? "" : s.trim();
        // Upgrade migration is disabled in the handoff build: old saved addresses
        // are left untouched so a private deployment is never rewritten.
        if (legacyLanUrl.equals(s)) return defaultUrl;
        return s;
    }

    private void saveUrl(String u) {
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, u).apply();
    }

    private void showUrlDialog(final boolean required) {
        final EditText et = new EditText(this);
        et.setSingleLine(true);
        // 地址里全是符号，用 URI 键盘：带 / 和 .，且不自动纠错、不首字母大写
        et.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        et.setSelectAllOnFocus(true);          // 获得焦点即全选，输入直接覆盖旧地址
        String cur = loadUrl();
        et.setText((cur != null && !cur.isEmpty()) ? cur : defaultUrl);
        et.setSelection(et.getText().length());

        AlertDialog.Builder b = new AlertDialog.Builder(this);
        b.setTitle(required ? (zh ? "设置后台地址" : "Set backend address")
                            : (zh ? "修改后台地址" : "Change backend address"));
        b.setMessage(zh
                ? "互联网地址 " + defaultUrl + "\n在家也可填局域网地址（同一 Wi-Fi 下更快）\n（地址栏内长按 / 点「粘贴」可直接粘贴；长按页面空白处可再次修改）"
                : "Internet address " + defaultUrl + "\nOn the same Wi-Fi you may use a LAN address instead\n(long-press the field or tap Paste; long-press blank area to reopen this)");
        b.setView(et);
        b.setCancelable(!required);
        b.setPositiveButton(zh ? "确定" : "OK", new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                String u = et.getText().toString().trim();
                if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://" + u;
                if (!u.endsWith("/")) u = u + "/";
                saveUrl(u);
                baseUrl = u;
                web.loadUrl(u);
            }
        });
        b.setNeutralButton(zh ? "粘贴" : "Paste", null);
        if (!required) b.setNegativeButton(zh ? "取消" : "Cancel", null);

        final AlertDialog dlg = b.create();
        dlg.show();
        // 重绑 neutral 的点击：默认行为会关掉对话框，这里只想把剪贴板内容填进去。
        dlg.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                String clip = readClipboard();
                if (clip == null || clip.trim().isEmpty()) {
                    toast(zh ? "剪贴板为空" : "Clipboard is empty");
                    return;
                }
                String u = clip.trim();
                et.setText(u);
                et.setSelection(u.length());
            }
        });
    }

    /** 粘贴「复制的地址」时，很多来源会带换行/空白，这里统一裁掉首尾空白。 */
    private String readClipboard() {
        try {
            android.content.ClipboardManager cm = (android.content.ClipboardManager)
                    getSystemService(CLIPBOARD_SERVICE);
            if (cm == null || !cm.hasPrimaryClip()) return null;
            ClipData clip = cm.getPrimaryClip();
            if (clip == null || clip.getItemCount() == 0) return null;
            CharSequence t = clip.getItemAt(0).coerceToText(this);
            return t == null ? null : t.toString().trim();
        } catch (Exception e) {
            return null;
        }
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }
}
