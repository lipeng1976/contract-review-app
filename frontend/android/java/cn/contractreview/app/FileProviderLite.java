package cn.contractreview.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Environment;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

/**
 * 极简文件提供器 —— 替代 androidx FileProvider。
 *
 * 本项目不用 Gradle，拿不到 androidx 依赖，而 API 24+ 又不允许把 file:// 交给
 * 外部应用（FileUriExposedException），所以必须自己实现一个 ContentProvider，
 * 通过 content:// 把下载好的 PDF / CSV 交给系统阅读器。
 *
 * 只暴露一个目录（应用外部私有目录下的 downloads/），只读，路径做 canonical 校验。
 */
public class FileProviderLite extends ContentProvider {

    public static final String AUTHORITY = "cn.contractreview.app.files";

    /** 下载目录：外部私有目录优先，拿不到时退回内部目录。 */
    public static File baseDir(Context ctx) {
        File d = ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (d == null) d = new File(ctx.getFilesDir(), "downloads");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    public static Uri uriFor(Context ctx, File f) {
        return Uri.parse("content://" + AUTHORITY + "/" + Uri.encode(f.getName()));
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    private File resolve(Uri uri) {
        String name = uri.getLastPathSegment();
        if (name == null || name.isEmpty()) return null;
        File base = baseDir(getContext());
        File f = new File(base, name);
        try {
            String root = base.getCanonicalPath() + File.separator;
            if (!f.getCanonicalPath().startsWith(root)) return null;
        } catch (IOException e) {
            return null;
        }
        return f.isFile() ? f : null;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File f = resolve(uri);
        if (f == null) throw new FileNotFoundException(String.valueOf(uri));
        if (mode != null && mode.contains("w")) throw new FileNotFoundException("read-only provider");
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public String getType(Uri uri) {
        File f = resolve(uri);
        return f == null ? null : MainActivity.mimeOf(f.getName());
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File f = resolve(uri);
        if (f == null) return null;
        String[] cols = (projection != null && projection.length > 0)
                ? projection
                : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
            else row[i] = null;
        }
        c.addRow(row);
        return c;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) { return null; }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) { return 0; }
}
