package com.flowclip.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

final class FileStore {
    private static final String PREFS = "flowclip_files_v1";
    private static final String KEY_REVISION = "revision";
    private static final String KEY_INDEX = "index";
    private static final long RESERVATION_TTL_MILLIS = 10L * 60L * 1000L;
    private static final int MAX_PENDING = 8;

    private final Context context;
    private final long maximumBytes;
    private final LinkedHashMap<String, StoredFile> files = new LinkedHashMap<>();
    private final Map<String, Reservation> reservations = new LinkedHashMap<>();
    private final Map<String, Integer> readers = new LinkedHashMap<>();
    private long revision;
    private volatile boolean closed;

    FileStore(Context context, long maximumBytes) {
        this.context = context.getApplicationContext();
        this.maximumBytes = maximumBytes;
        load();
    }

    long maximumBytes() {
        return maximumBytes;
    }

    synchronized MutationResult reserve(FileMetadata requested) throws IOException {
        ensureOpen();
        if (requested.size > maximumBytes) throw new LimitExceededException("文件超过大小限制");
        cleanupReservations();
        if (files.containsKey(requested.id) || reservations.containsKey(requested.id)) {
            throw new ConflictException("文件 ID 已被使用");
        }
        if (files.size() + reservations.size() >= FileTransferPolicy.MAX_LOCAL_FILES) {
            throw new ConflictException("文件列表已满，请先删除不需要的文件");
        }
        if (reservations.size() >= MAX_PENDING) {
            throw new ConflictException("待上传文件过多，请稍后重试");
        }
        long previousRevision = revision;
        reservations.put(requested.id, new Reservation(requested));
        revision += 1L;
        if (!persistLocked()) {
            reservations.remove(requested.id);
            revision = previousRevision;
            throw new IOException("无法保存文件索引");
        }
        return new MutationResult(revision, requested);
    }

    MutationResult put(String id, InputStream input, long contentLength) throws IOException {
        Reservation reservation;
        synchronized (this) {
            ensureOpen();
            StoredFile complete = files.get(id);
            if (complete != null) throw new ConflictException("文件已经上传完成");
            reservation = reservations.get(id);
            if (reservation == null) throw new ConflictException("请先提交文件元数据");
            if (reservation.uploading) throw new ConflictException("同一文件正在上传");
            if (contentLength != reservation.metadata.size) {
                throw new ProtocolException("文件长度不匹配");
            }
            reservation.uploading = true;
        }

        FileStorage.PendingFile target = null;
        try {
            target = FileStorage.begin(context, reservation.metadata);
            MessageDigest digest = newDigest();
            long remaining = contentLength;
            byte[] buffer = new byte[64 * 1024];
            OutputStream output = target.output();
            while (remaining > 0L) {
                if (closed || Thread.currentThread().isInterrupted()) {
                    throw new IOException("文件上传已取消");
                }
                int expected = (int) Math.min((long) buffer.length, remaining);
                int read = input.read(buffer, 0, expected);
                if (read < 0) throw new IOException("文件数据提前结束");
                if (read == 0) continue;
                output.write(buffer, 0, read);
                digest.update(buffer, 0, read);
                remaining -= read;
            }
            output.flush();
            target.finish();
            FileMetadata completed;
            long completedRevision;
            synchronized (this) {
                ensureOpen();
                revision += 1L;
                completedRevision = revision;
                completed = reservation.metadata.completed(hex(digest.digest()));
                files.put(
                        completed.id,
                        new StoredFile(completed, target.uri, target.displayPath));
                reservations.remove(completed.id);
                if (!persistLocked()) {
                    files.remove(completed.id);
                    revision -= 1L;
                    reservations.put(completed.id, reservation);
                    persistLocked();
                    throw new IOException("无法保存文件索引");
                }
            }
            return new MutationResult(completedRevision, completed);
        } catch (IOException | RuntimeException exception) {
            if (target != null) target.discard();
            throw exception;
        } finally {
            synchronized (this) {
                Reservation current = reservations.get(id);
                if (current != null) current.uploading = false;
            }
        }
    }

    synchronized Snapshot snapshot() {
        List<FileMetadata> result = new ArrayList<>();
        for (StoredFile file : files.values()) result.add(file.metadata);
        return new Snapshot(revision, result);
    }

    synchronized OpenedFile open(String id) throws IOException {
        ensureOpen();
        StoredFile stored = files.get(id);
        if (stored == null) throw new NotFoundException("文件不存在");
        InputStream source = FileStorage.open(context, stored.uri);
        readers.put(id, readers.getOrDefault(id, 0) + 1);
        InputStream tracked = new FilterInputStream(source) {
            private boolean closed;

            @Override
            public synchronized void close() throws IOException {
                if (closed) return;
                closed = true;
                try {
                    super.close();
                } finally {
                    releaseReader(id);
                }
            }
        };
        return new OpenedFile(stored, tracked);
    }

    synchronized StoredFile stored(String id) {
        return files.get(id);
    }

    synchronized long delete(String id) throws IOException {
        ensureOpen();
        cleanupReservations();
        Reservation pending = reservations.get(id);
        if (pending != null) {
            if (pending.uploading) throw new ConflictException("文件正在传输");
            long previousRevision = revision;
            reservations.remove(id);
            revision += 1L;
            if (!persistLocked()) {
                reservations.put(id, pending);
                revision = previousRevision;
                throw new IOException("无法保存文件索引");
            }
            return revision;
        }
        StoredFile stored = files.get(id);
        if (stored == null) throw new NotFoundException("文件不存在");
        if (readers.getOrDefault(id, 0) > 0) {
            throw new ConflictException("文件正在下载，请稍后删除");
        }

        long previousRevision = revision;
        files.remove(id);
        revision += 1L;
        if (!persistLocked()) {
            files.put(id, stored);
            revision = previousRevision;
            persistLocked();
            throw new IOException("无法保存文件索引");
        }
        try {
            FileStorage.delete(context, stored.uri);
        } catch (IOException | RuntimeException exception) {
            files.put(id, stored);
            revision = previousRevision;
            if (!persistLocked()) {
                throw new IOException("删除文件失败且无法恢复文件索引", exception);
            }
            throw exception;
        }
        return revision;
    }

    synchronized void closeStore() {
        closed = true;
    }

    private void ensureOpen() throws IOException {
        if (closed) throw new IOException("手机服务器正在停止");
    }

    private synchronized void releaseReader(String id) {
        int remaining = readers.getOrDefault(id, 0) - 1;
        if (remaining > 0) readers.put(id, remaining);
        else readers.remove(id);
    }

    private void cleanupReservations() throws IOException {
        long cutoff = System.currentTimeMillis() - RESERVATION_TTL_MILLIS;
        Map<String, Reservation> expired = new LinkedHashMap<>();
        for (Map.Entry<String, Reservation> entry : reservations.entrySet()) {
            if (!entry.getValue().uploading && entry.getValue().createdAt < cutoff) {
                expired.put(entry.getKey(), entry.getValue());
            }
        }
        if (expired.isEmpty()) return;
        long previousRevision = revision;
        for (String id : expired.keySet()) reservations.remove(id);
        revision += 1L;
        if (!persistLocked()) {
            reservations.putAll(expired);
            revision = previousRevision;
            throw new IOException("无法保存文件索引");
        }
    }

    private void load() {
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        revision = Math.max(0L, preferences.getLong(KEY_REVISION, 0L));
        String encoded = preferences.getString(KEY_INDEX, "[]");
        if (encoded == null) return;
        try {
            JSONArray list = new JSONArray(encoded);
            for (int index = 0; index < list.length(); index++) {
                JSONObject raw = list.getJSONObject(index);
                if (!raw.has("status") && raw.has("sha256")) {
                    raw.put("status", "ready");
                }
                FileMetadata metadata = FileMetadata.fromJson(raw, Long.MAX_VALUE);
                if (!"ready".equals(metadata.status)) throw new JSONException("文件状态无效");
                Uri uri = Uri.parse(raw.getString("uri"));
                String displayPath = raw.optString("displayPath", metadata.filename);
                files.put(metadata.id, new StoredFile(metadata, uri, displayPath));
            }
        } catch (JSONException | RuntimeException ignored) {
            files.clear();
        }
    }

    private boolean persistLocked() {
        JSONArray list = new JSONArray();
        try {
            for (StoredFile stored : files.values()) {
                JSONObject raw = stored.metadata.toJson();
                raw.put("uri", stored.uri.toString());
                raw.put("displayPath", stored.displayPath);
                list.put(raw);
            }
        } catch (JSONException impossible) {
            throw new AssertionError(impossible);
        }
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putLong(KEY_REVISION, revision)
                .putString(KEY_INDEX, list.toString())
                .commit();
    }

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            result.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        }
        return result.toString();
    }

    static final class StoredFile {
        final FileMetadata metadata;
        final Uri uri;
        final String displayPath;

        StoredFile(FileMetadata metadata, Uri uri, String displayPath) {
            this.metadata = metadata;
            this.uri = uri;
            this.displayPath = displayPath;
        }
    }

    static final class OpenedFile implements AutoCloseable {
        final StoredFile stored;
        final InputStream input;

        OpenedFile(StoredFile stored, InputStream input) {
            this.stored = stored;
            this.input = input;
        }

        @Override
        public void close() throws IOException {
            input.close();
        }
    }

    static final class Snapshot {
        final long revision;
        final List<FileMetadata> files;

        Snapshot(long revision, List<FileMetadata> files) {
            this.revision = revision;
            this.files = files;
        }

        JSONObject toJson() throws JSONException {
            JSONArray array = new JSONArray();
            for (FileMetadata file : files) array.put(file.toJson());
            JSONObject result = new JSONObject();
            result.put("revision", revision);
            result.put("files", array);
            return result;
        }
    }

    static final class MutationResult {
        final long revision;
        final FileMetadata metadata;

        MutationResult(long revision, FileMetadata metadata) {
            this.revision = revision;
            this.metadata = metadata;
        }
    }

    private static final class Reservation {
        final FileMetadata metadata;
        final long createdAt = System.currentTimeMillis();
        boolean uploading;

        Reservation(FileMetadata metadata) {
            this.metadata = metadata;
        }
    }

    static final class NotFoundException extends IOException {
        NotFoundException(String message) {
            super(message);
        }
    }

    static final class ConflictException extends IOException {
        ConflictException(String message) {
            super(message);
        }
    }

    static final class LimitExceededException extends IOException {
        LimitExceededException(String message) {
            super(message);
        }
    }

    static final class ProtocolException extends IOException {
        ProtocolException(String message) {
            super(message);
        }
    }
}
