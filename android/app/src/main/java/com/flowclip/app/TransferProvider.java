package com.flowclip.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.webkit.MimeTypeMap;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.List;
import java.util.Locale;

public final class TransferProvider extends ContentProvider {
    static Uri uriForFile(Context context, File file) throws IOException {
        File allowed = FileStorage.appSpecificDirectory(context).getCanonicalFile();
        File canonical = file.getCanonicalFile();
        if (!isChild(allowed, canonical)) throw new IOException("文件路径超出允许目录");
        return new Uri.Builder()
                .scheme("content")
                .authority(context.getPackageName() + ".files")
                .appendPath("file")
                .appendPath(canonical.getName())
                .build();
    }

    static File fileForUri(Context context, Uri uri) throws IOException {
        if (uri == null
                || !"content".equals(uri.getScheme())
                || !(context.getPackageName() + ".files").equals(uri.getAuthority())) {
            throw new IOException("文件 URI 无效");
        }
        List<String> segments = uri.getPathSegments();
        if (segments.size() != 2 || !"file".equals(segments.get(0))) {
            throw new IOException("文件 URI 无效");
        }
        String name = segments.get(1);
        try {
            if (!name.equals(FileMetadata.sanitizeFilename(name))) {
                throw new IOException("文件名称无效");
            }
        } catch (IllegalArgumentException exception) {
            throw new IOException("文件名称无效", exception);
        }
        File allowed = FileStorage.appSpecificDirectory(context).getCanonicalFile();
        File target = new File(allowed, name).getCanonicalFile();
        if (!isChild(allowed, target)) throw new IOException("文件路径超出允许目录");
        return target;
    }

    private static boolean isChild(File root, File target) {
        return target.getPath().startsWith(root.getPath() + File.separator);
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        if (isIncomingUri(uri)) {
            try {
                return IncomingFileRegistry.sourceForUri(uri).mime;
            } catch (FileNotFoundException ignored) {
                return "application/octet-stream";
            }
        }
        String name = uri == null ? null : uri.getLastPathSegment();
        if (name == null) return "application/octet-stream";
        int dot = name.lastIndexOf('.');
        if (dot >= 0 && dot < name.length() - 1) {
            String extension = name.substring(dot + 1).toLowerCase(Locale.ROOT);
            String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
            if (mime != null) return mime;
        }
        return "application/octet-stream";
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode) || getContext() == null) {
            throw new FileNotFoundException("不支持的文件访问模式");
        }
        if (isIncomingUri(uri)) {
            try {
                return IncomingFileRegistry.sourceForUri(uri).duplicate();
            } catch (IOException exception) {
                throw new FileNotFoundException(exception.getMessage());
            }
        }
        try {
            File target = fileForUri(getContext(), uri);
            if (!target.isFile()) throw new FileNotFoundException("文件不存在");
            return ParcelFileDescriptor.open(target, ParcelFileDescriptor.MODE_READ_ONLY);
        } catch (IOException exception) {
            throw new FileNotFoundException(exception.getMessage());
        }
    }

    @Override
    public Cursor query(
            Uri uri,
            String[] projection,
            String selection,
            String[] selectionArgs,
            String sortOrder) {
        String[] columns = projection == null
                ? new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}
                : projection;
        MatrixCursor cursor = new MatrixCursor(columns, 1);
        if (getContext() == null) return cursor;
        try {
            String displayName;
            long size;
            if (isIncomingUri(uri)) {
                IncomingFileRegistry.Source source = IncomingFileRegistry.sourceForUri(uri);
                displayName = source.filename;
                size = source.size;
            } else {
                File file = fileForUri(getContext(), uri);
                if (!file.isFile()) return cursor;
                displayName = file.getName();
                size = file.length();
            }
            Object[] row = new Object[columns.length];
            for (int index = 0; index < columns.length; index++) {
                if (OpenableColumns.DISPLAY_NAME.equals(columns[index])) row[index] = displayName;
                else if (OpenableColumns.SIZE.equals(columns[index])) {
                    row[index] = size >= 0L ? size : null;
                }
                else row[index] = null;
            }
            cursor.addRow(row);
        } catch (IOException ignored) {
            // Return an empty cursor for an invalid URI.
        }
        return cursor;
    }

    private static boolean isIncomingUri(Uri uri) {
        List<String> segments = uri == null ? java.util.Collections.emptyList()
                : uri.getPathSegments();
        return segments.size() == 3 && "incoming".equals(segments.get(0));
    }

    @Override public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection,
            String[] selectionArgs) { return 0; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
}
