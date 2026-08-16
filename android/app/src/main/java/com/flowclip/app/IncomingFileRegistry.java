package com.flowclip.app;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.Closeable;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

final class IncomingFileRegistry {
    private static final int MAX_PENDING_BATCHES = 4;
    private static final Map<String, Batch> PENDING = new ConcurrentHashMap<>();

    private IncomingFileRegistry() {}

    static StagedBatch stage(Context context, List<Uri> uris) throws IOException {
        IncomingContentPolicy.requireItemCount(uris.size());
        if (uris.isEmpty()) throw new IOException("没有可读取的文件");
        if (PENDING.size() >= MAX_PENDING_BATCHES) {
            throw new IOException("等待发送的文件批次过多");
        }

        ContentResolver resolver = context.getContentResolver();
        List<Source> sources = new ArrayList<>();
        try {
            for (Uri uri : uris) {
                sources.add(openSource(resolver, context.getPackageName(), uri));
            }
        } catch (IOException exception) {
            closeAll(sources);
            throw exception;
        } catch (RuntimeException exception) {
            closeAll(sources);
            throw new IOException("无法打开拖入文件", exception);
        }

        String token = UUID.randomUUID().toString();
        Batch batch = new Batch(sources);
        if (PENDING.putIfAbsent(token, batch) != null) {
            batch.close();
            throw new IOException("无法登记待发送文件");
        }
        List<Uri> internalUris = new ArrayList<>();
        for (int index = 0; index < sources.size(); index++) {
            internalUris.add(internalUri(context, token, index));
        }
        return new StagedBatch(token, Collections.unmodifiableList(internalUris));
    }

    static void discard(String token) {
        if (token == null || token.isEmpty()) return;
        Batch batch = PENDING.remove(token);
        if (batch != null) batch.close();
    }

    static Source sourceForUri(Uri uri) throws FileNotFoundException {
        List<String> segments = uri == null ? Collections.emptyList() : uri.getPathSegments();
        if (segments.size() != 3 || !"incoming".equals(segments.get(0))) {
            throw new FileNotFoundException("临时文件 URI 无效");
        }
        Batch batch = PENDING.get(segments.get(1));
        if (batch == null) throw new FileNotFoundException("临时文件授权已过期");
        final int index;
        try {
            index = Integer.parseInt(segments.get(2));
        } catch (NumberFormatException exception) {
            throw new FileNotFoundException("临时文件 URI 无效");
        }
        if (index < 0 || index >= batch.sources.size()) {
            throw new FileNotFoundException("临时文件 URI 无效");
        }
        return batch.sources.get(index);
    }

    private static Source openSource(
            ContentResolver resolver, String packageName, Uri uri) throws IOException {
        if (uri == null || !"content".equalsIgnoreCase(uri.getScheme())) {
            throw new IOException("仅支持系统提供的 content:// 文件");
        }
        if (IncomingContentPolicy.isAppPrivateAuthority(packageName, uri.getAuthority())) {
            throw new IOException("不接受 FlowClip 私有内容 URI");
        }
        String filename = queryName(resolver, uri);
        String mime = resolver.getType(uri);
        if (mime == null || mime.trim().isEmpty()) mime = "application/octet-stream";
        try {
            mime = FileMetadata.validateMime(mime);
        } catch (IllegalArgumentException exception) {
            mime = "application/octet-stream";
        }
        long size = querySize(resolver, uri);
        ParcelFileDescriptor descriptor = resolver.openFileDescriptor(uri, "r");
        if (descriptor == null) throw new IOException("无法打开拖入文件");
        if (size < 0L) size = descriptor.getStatSize();
        return new Source(filename, mime, Math.max(-1L, size), descriptor);
    }

    private static String queryName(ContentResolver resolver, Uri uri) {
        try (Cursor cursor = resolver.query(
                uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                String value = cursor.getString(0);
                if (value != null && !value.trim().isEmpty()) {
                    return FileMetadata.sanitizeFilename(value);
                }
            }
        } catch (RuntimeException ignored) {
            // A generated portable filename is used when provider metadata is unavailable.
        }
        return "FlowClip_" + System.currentTimeMillis() + ".bin";
    }

    private static long querySize(ContentResolver resolver, Uri uri) {
        try (Cursor cursor = resolver.query(
                uri, new String[]{OpenableColumns.SIZE}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst() && !cursor.isNull(0)) {
                return cursor.getLong(0);
            }
        } catch (RuntimeException ignored) {
            // The descriptor stat or streaming size check remains authoritative.
        }
        return -1L;
    }

    private static Uri internalUri(Context context, String token, int index) {
        return new Uri.Builder()
                .scheme("content")
                .authority(context.getPackageName() + ".files")
                .appendPath("incoming")
                .appendPath(token)
                .appendPath(String.valueOf(index))
                .build();
    }

    private static void closeAll(List<? extends Closeable> values) {
        for (Closeable value : values) {
            try {
                value.close();
            } catch (IOException ignored) {
                // Continue closing the remaining descriptors.
            }
        }
    }

    static final class StagedBatch {
        final String token;
        final List<Uri> uris;

        StagedBatch(String token, List<Uri> uris) {
            this.token = token;
            this.uris = uris;
        }
    }

    static final class Source implements Closeable {
        final String filename;
        final String mime;
        final long size;
        private ParcelFileDescriptor descriptor;

        Source(String filename, String mime, long size, ParcelFileDescriptor descriptor) {
            this.filename = filename;
            this.mime = mime;
            this.size = size;
            this.descriptor = descriptor;
        }

        synchronized ParcelFileDescriptor duplicate() throws IOException {
            if (descriptor == null) throw new FileNotFoundException("临时文件授权已过期");
            return ParcelFileDescriptor.dup(descriptor.getFileDescriptor());
        }

        @Override
        public synchronized void close() throws IOException {
            if (descriptor == null) return;
            descriptor.close();
            descriptor = null;
        }
    }

    private static final class Batch implements Closeable {
        final List<Source> sources;

        Batch(List<Source> sources) {
            this.sources = sources;
        }

        @Override
        public void close() {
            closeAll(sources);
        }
    }
}
