package com.flowclip.app;

import android.content.ContentProvider;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.UriMatcher;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Environment;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

public final class ImageProvider extends ContentProvider {
    static final String AUTHORITY = "com.flowclip.app.images";
    private static final int IMAGE = 1;
    private static final UriMatcher MATCHER = new UriMatcher(UriMatcher.NO_MATCH);

    static {
        MATCHER.addURI(AUTHORITY, "image/*", IMAGE);
    }

    static Uri uriForFile(Context context, File file) throws IOException {
        File root = context.getExternalFilesDir(Environment.DIRECTORY_PICTURES);
        if (root == null) {
            root = new File(context.getFilesDir(), "Pictures");
        }
        File allowed = new File(root, "FlowClip").getCanonicalFile();
        File canonical = file.getCanonicalFile();
        if (!canonical.getPath().startsWith(allowed.getPath() + File.separator)) {
            throw new IOException("图片路径超出允许目录");
        }
        return new Uri.Builder()
                .scheme(ContentResolver.SCHEME_CONTENT)
                .authority(AUTHORITY)
                .appendPath("image")
                .appendPath(canonical.getName())
                .build();
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        String name = uri.getLastPathSegment();
        if (name == null) {
            return "image/*";
        }
        String lower = name.toLowerCase(java.util.Locale.ROOT);
        if (lower.endsWith(".png")) return "image/png";
        if (lower.endsWith(".webp")) return "image/webp";
        if (lower.endsWith(".gif")) return "image/gif";
        return "image/jpeg";
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (MATCHER.match(uri) != IMAGE || !"r".equals(mode)) {
            throw new FileNotFoundException("不支持的图片 URI 或访问模式");
        }
        File target;
        try {
            target = resolve(uri);
        } catch (IOException exception) {
            throw new FileNotFoundException(exception.getMessage());
        }
        return ParcelFileDescriptor.open(target, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(
            Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        String[] columns = projection == null
                ? new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}
                : projection;
        MatrixCursor cursor = new MatrixCursor(columns, 1);
        try {
            File file = resolve(uri);
            Object[] values = new Object[columns.length];
            for (int index = 0; index < columns.length; index++) {
                if (OpenableColumns.DISPLAY_NAME.equals(columns[index])) values[index] = file.getName();
                else if (OpenableColumns.SIZE.equals(columns[index])) values[index] = file.length();
                else values[index] = null;
            }
            cursor.addRow(values);
        } catch (IOException ignored) {
            // Return an empty cursor for an invalid URI.
        }
        return cursor;
    }

    private File resolve(Uri uri) throws IOException {
        if (getContext() == null || MATCHER.match(uri) != IMAGE) {
            throw new IOException("图片 URI 无效");
        }
        String name = uri.getLastPathSegment();
        if (name == null || !name.equals(ClipboardBridge.sanitizeName(name))) {
            throw new IOException("图片名称无效");
        }
        File root = getContext().getExternalFilesDir(Environment.DIRECTORY_PICTURES);
        if (root == null) root = new File(getContext().getFilesDir(), "Pictures");
        File allowed = new File(root, "FlowClip").getCanonicalFile();
        File target = new File(allowed, name).getCanonicalFile();
        if (!target.getPath().startsWith(allowed.getPath() + File.separator) || !target.isFile()) {
            throw new IOException("图片不存在");
        }
        return target;
    }

    @Override public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) { return 0; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
}
