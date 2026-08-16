package com.flowclip.app;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

final class FileClient {
    interface ProgressListener {
        void onProgress(String stage, long completed, long total);
    }

    private static final int JSON_LIMIT = 256 * 1024;

    private final Context context;
    private final AppConfig config;

    FileClient(Context context, AppConfig config) {
        config.validate();
        this.context = context.getApplicationContext();
        this.config = config;
    }

    ListResult list(Cancellation cancellation) throws IOException, JSONException {
        HttpURLConnection connection = open("GET", "/api/v1/files");
        try {
            cancellation.attach(connection);
            JSONObject raw = requireJsonResponse(connection, HttpURLConnection.HTTP_OK, JSON_LIMIT);
            long revision = requiredNonNegativeLong(raw, "revision");
            JSONArray values = raw.getJSONArray("files");
            if (values.length() > FileTransferPolicy.MAX_REMOTE_FILES) {
                throw new JSONException("服务器文件列表过长");
            }
            List<FileMetadata> files = new ArrayList<>();
            Set<String> ids = new HashSet<>();
            for (int index = 0; index < values.length(); index++) {
                FileMetadata metadata = FileMetadata.fromJson(
                        values.getJSONObject(index), Long.MAX_VALUE);
                if (!"ready".equals(metadata.status) || !ids.add(metadata.id)) {
                    throw new JSONException("服务器文件列表无效");
                }
                files.add(metadata);
            }
            return new ListResult(revision, files);
        } finally {
            cancellation.detach(connection);
            connection.disconnect();
        }
    }

    FileMetadata upload(
            Uri source,
            ProgressListener progress,
            Cancellation cancellation) throws IOException, JSONException {
        PreparedUpload prepared = prepare(source, progress, cancellation);
        FileMetadata metadata = null;
        boolean pendingCreated = false;
        boolean uploadConfirmed = false;
        try {
            metadata = FileMetadata.createUpload(
                    config.deviceId, prepared.filename, prepared.mime, prepared.file.length());
            byte[] reservationBody = metadata.toUploadJson().toString()
                    .getBytes(StandardCharsets.UTF_8);
            if (reservationBody.length > FileTransferPolicy.MAX_METADATA_BYTES) {
                throw new IOException("文件元数据超过大小限制");
            }
            HttpURLConnection reservation = open("POST", "/api/v1/files");
            try {
                reservation.setDoOutput(true);
                reservation.setFixedLengthStreamingMode(reservationBody.length);
                reservation.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                cancellation.attach(reservation);
                try (OutputStream output = reservation.getOutputStream()) {
                    output.write(reservationBody);
                }
                int code = reservation.getResponseCode();
                if (code != HttpURLConnection.HTTP_CREATED) throw failure(reservation);
                pendingCreated = true;
                JSONObject response = readJson(reservation, JSON_LIMIT);
                requireMutationEnvelope(response);
                FileMetadata reserved = FileMetadata.fromJson(
                        response.getJSONObject("file"), config.fileMaxBytes());
                if (!metadata.sameUpload(reserved) || !"pending".equals(reserved.status)) {
                    throw new IOException("服务器返回了不匹配的文件元数据");
                }
            } finally {
                cancellation.detach(reservation);
                reservation.disconnect();
            }

            HttpURLConnection upload = open("PUT", "/api/v1/files/" + metadata.id);
            try {
                upload.setDoOutput(true);
                upload.setFixedLengthStreamingMode(metadata.size);
                upload.setRequestProperty("Content-Type", "application/octet-stream");
                cancellation.attach(upload);
                MessageDigest digest = newDigest();
                long completed = 0L;
                try (InputStream input = new FileInputStream(prepared.file);
                     OutputStream output = upload.getOutputStream()) {
                    byte[] buffer = new byte[64 * 1024];
                    int read;
                    while ((read = input.read(buffer)) != -1) {
                        cancellation.throwIfCancelled();
                        output.write(buffer, 0, read);
                        digest.update(buffer, 0, read);
                        completed += read;
                        notifyProgress(progress, "正在上传", completed, metadata.size);
                    }
                }
                JSONObject response = requireJsonResponse(
                        upload, HttpURLConnection.HTTP_OK, JSON_LIMIT);
                requireMutationEnvelope(response);
                FileMetadata stored = FileMetadata.fromJson(
                        response.getJSONObject("file"), config.fileMaxBytes());
                String checksum = hex(digest.digest());
                if (!metadata.id.equals(stored.id)
                        || !"ready".equals(stored.status)
                        || stored.size != metadata.size
                        || !checksum.equals(stored.sha256)) {
                    throw new IOException("服务器文件校验失败");
                }
                uploadConfirmed = true;
                return stored;
            } finally {
                cancellation.detach(upload);
                upload.disconnect();
            }
        } finally {
            if (pendingCreated && !uploadConfirmed && metadata != null) {
                abortRemote(metadata.id);
            }
            if (prepared.file.exists()) prepared.file.delete();
        }
    }

    DownloadedFile download(
            FileMetadata metadata,
            ProgressListener progress,
            Cancellation cancellation) throws IOException {
        if (!"ready".equals(metadata.status) || metadata.sha256 == null) {
            throw new IOException("文件尚未上传完成");
        }
        if (metadata.size > config.fileMaxBytes()) throw new IOException("文件超过大小限制");
        HttpURLConnection connection = open("GET", "/api/v1/files/" + metadata.id);
        FileStorage.PendingFile target = null;
        try {
            cancellation.attach(connection);
            int code = connection.getResponseCode();
            if (code != HttpURLConnection.HTTP_OK) throw failure(connection);
            long declared = connection.getContentLengthLong();
            if (declared != metadata.size) throw new IOException("服务器文件长度不匹配");
            target = FileStorage.begin(context, metadata);
            MessageDigest digest = newDigest();
            long completed = 0L;
            try (InputStream input = connection.getInputStream()) {
                OutputStream output = target.output();
                byte[] buffer = new byte[64 * 1024];
                while (completed < metadata.size) {
                    cancellation.throwIfCancelled();
                    int expected = (int) Math.min((long) buffer.length, metadata.size - completed);
                    int read = input.read(buffer, 0, expected);
                    if (read < 0) throw new IOException("下载文件提前结束");
                    if (read == 0) continue;
                    output.write(buffer, 0, read);
                    digest.update(buffer, 0, read);
                    completed += read;
                    notifyProgress(progress, "正在下载", completed, metadata.size);
                }
                output.flush();
            }
            if (!metadata.sha256.equals(hex(digest.digest()))) {
                throw new IOException("下载文件校验失败");
            }
            target.finish();
            return new DownloadedFile(target.uri, target.displayPath, metadata);
        } catch (IOException | RuntimeException exception) {
            if (target != null) target.discard();
            throw exception;
        } finally {
            cancellation.detach(connection);
            connection.disconnect();
        }
    }

    long delete(String id, Cancellation cancellation) throws IOException, JSONException {
        HttpURLConnection connection = open("DELETE", "/api/v1/files/" + id);
        try {
            cancellation.attach(connection);
            JSONObject result = requireJsonResponse(
                    connection, HttpURLConnection.HTTP_OK, JSON_LIMIT);
            if (!Boolean.TRUE.equals(result.opt("ok"))) {
                throw new JSONException("服务器删除响应无效");
            }
            return requiredNonNegativeLong(result, "revision");
        } finally {
            cancellation.detach(connection);
            connection.disconnect();
        }
    }

    private PreparedUpload prepare(
            Uri source,
            ProgressListener progress,
            Cancellation cancellation) throws IOException {
        ContentResolver resolver = context.getContentResolver();
        String filename = queryFilename(resolver, source);
        String mime = resolver.getType(source);
        if (mime == null || mime.trim().isEmpty()) mime = "application/octet-stream";
        mime = FileMetadata.validateMime(mime);
        File directory = new File(context.getCacheDir(), "flowclip_outgoing");
        if (!directory.exists() && !directory.mkdirs()) {
            throw new IOException("无法创建发送缓存");
        }
        File temporary = new File(directory, UUID.randomUUID() + ".part");
        long knownSize = querySize(resolver, source);
        if (knownSize > config.fileMaxBytes()) throw new IOException("文件超过大小限制");
        long total = 0L;
        try (InputStream input = resolver.openInputStream(source);
             OutputStream output = new FileOutputStream(temporary)) {
            if (input == null) throw new IOException("无法读取所选文件");
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = input.read(buffer)) != -1) {
                cancellation.throwIfCancelled();
                total += read;
                if (total > config.fileMaxBytes()) throw new IOException("文件超过大小限制");
                output.write(buffer, 0, read);
                notifyProgress(progress, "正在准备文件", total, knownSize > 0L ? knownSize : total);
            }
        } catch (SecurityException exception) {
            temporary.delete();
            throw new IOException("没有权限读取所选文件", exception);
        } catch (IOException | RuntimeException exception) {
            temporary.delete();
            throw exception;
        }
        return new PreparedUpload(temporary, filename, mime);
    }

    private HttpURLConnection open(String method, String path) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(
                config.effectiveServerUrl() + path).openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(60_000);
        connection.setUseCaches(false);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("Authorization", "Bearer " + config.token);
        connection.setRequestProperty("User-Agent", "FlowClip-Android/1.4.0");
        connection.setRequestProperty("Connection", "close");
        return connection;
    }

    private void abortRemote(String id) {
        HttpURLConnection connection = null;
        try {
            connection = open("DELETE", "/api/v1/files/" + id);
            connection.setConnectTimeout(1_000);
            connection.setReadTimeout(1_000);
            int code = connection.getResponseCode();
            if (code == HttpURLConnection.HTTP_OK) {
                readLimited(connection.getInputStream(), JSON_LIMIT);
            } else if (code != HttpURLConnection.HTTP_NOT_FOUND) {
                InputStream error = connection.getErrorStream();
                if (error != null) readLimited(error, JSON_LIMIT);
            }
        } catch (IOException ignored) {
            // Best-effort cleanup; stale reservations also expire on the server.
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private static void requireMutationEnvelope(JSONObject response) throws JSONException {
        if (!Boolean.TRUE.equals(response.opt("ok"))) {
            throw new JSONException("服务器文件响应无效");
        }
        requiredNonNegativeLong(response, "revision");
        if (!(response.opt("file") instanceof JSONObject)) {
            throw new JSONException("服务器文件响应无效");
        }
    }

    private static long requiredNonNegativeLong(JSONObject raw, String key) throws JSONException {
        Object value = raw.opt(key);
        if (!(value instanceof Number)) throw new JSONException("文件修订号无效");
        try {
            long result = new java.math.BigDecimal(value.toString()).longValueExact();
            if (result < 0L) throw new JSONException("文件修订号无效");
            return result;
        } catch (NumberFormatException | ArithmeticException exception) {
            throw new JSONException("文件修订号无效");
        }
    }

    private static JSONObject requireJsonResponse(
            HttpURLConnection connection, int expected, int maximum)
            throws IOException, JSONException {
        int code = connection.getResponseCode();
        if (code != expected) throw failure(connection);
        return readJson(connection, maximum);
    }

    private static JSONObject readJson(HttpURLConnection connection, int maximum)
            throws IOException, JSONException {
        InputStream input = connection.getInputStream();
        byte[] body = readLimited(input, maximum);
        return new JSONObject(new String(body, StandardCharsets.UTF_8));
    }

    private static IOException failure(HttpURLConnection connection) throws IOException {
        int code = connection.getResponseCode();
        String message = "服务器返回 HTTP " + code;
        InputStream error = connection.getErrorStream();
        if (error != null) {
            try {
                JSONObject raw = new JSONObject(
                        new String(readLimited(error, 64 * 1024), StandardCharsets.UTF_8));
                String detail = raw.optString("error", "");
                if (!detail.isEmpty()) message = detail;
            } catch (JSONException ignored) {
                // Keep the status-code message.
            }
        }
        return new IOException(message);
    }

    private static byte[] readLimited(InputStream input, int maximum) throws IOException {
        try (InputStream source = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8 * 1024];
            int total = 0;
            int read;
            while ((read = source.read(buffer)) != -1) {
                total += read;
                if (total > maximum) throw new IOException("服务器响应超过大小限制");
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        }
    }

    private static String queryFilename(ContentResolver resolver, Uri uri) {
        try (Cursor cursor = resolver.query(
                uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                String value = cursor.getString(0);
                if (value != null && !value.trim().isEmpty()) {
                    return FileMetadata.sanitizeFilename(value);
                }
            }
        } catch (RuntimeException ignored) {
            // Fall through to a generated name.
        }
        return "FlowClip_" + System.currentTimeMillis() + ".bin";
    }

    private static long querySize(ContentResolver resolver, Uri uri) {
        try (Cursor cursor = resolver.query(
                uri, new String[]{OpenableColumns.SIZE}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst() && !cursor.isNull(0)) {
                return Math.max(0L, cursor.getLong(0));
            }
        } catch (RuntimeException ignored) {
            // Unknown sizes are discovered while spooling the stream.
        }
        return 0L;
    }

    private static void notifyProgress(
            ProgressListener listener, String stage, long completed, long total) {
        if (listener != null) listener.onProgress(stage, completed, total);
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

    static final class Cancellation {
        private volatile boolean cancelled;
        private volatile HttpURLConnection connection;

        void cancel() {
            cancelled = true;
            HttpURLConnection active = connection;
            if (active != null) active.disconnect();
        }

        void attach(HttpURLConnection value) throws IOException {
            connection = value;
            throwIfCancelled();
        }

        void detach(HttpURLConnection value) {
            if (connection == value) connection = null;
        }

        void throwIfCancelled() throws IOException {
            if (cancelled || Thread.currentThread().isInterrupted()) {
                throw new IOException("文件传输已取消");
            }
        }

        boolean isCancelled() {
            return cancelled || Thread.currentThread().isInterrupted();
        }
    }

    static final class ListResult {
        final long revision;
        final List<FileMetadata> files;

        ListResult(long revision, List<FileMetadata> files) {
            this.revision = revision;
            this.files = files;
        }

        JSONObject toJson() throws JSONException {
            JSONArray array = new JSONArray();
            for (FileMetadata file : files) array.put(file.toJson());
            return new JSONObject().put("revision", revision).put("files", array);
        }
    }

    static final class DownloadedFile {
        final Uri uri;
        final String displayPath;
        final FileMetadata metadata;

        DownloadedFile(Uri uri, String displayPath, FileMetadata metadata) {
            this.uri = uri;
            this.displayPath = displayPath;
            this.metadata = metadata;
        }
    }

    private static final class PreparedUpload {
        final File file;
        final String filename;
        final String mime;

        PreparedUpload(File file, String filename, String mime) {
            this.file = file;
            this.filename = filename;
            this.mime = mime;
        }
    }
}
