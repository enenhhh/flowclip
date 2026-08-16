package com.flowclip.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import fi.iki.elonen.NanoHTTPD;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

final class EmbeddedServer extends NanoHTTPD {
    private static final String LOG_TAG = "FlowClipServer";
    interface Listener {
        void onClipboardReceived(ClipItem item);
        void onFilesChanged(FileStore.Snapshot snapshot, FileStore.StoredFile received);
    }

    private static final int MAX_WORKERS = 4;
    private static final int MAX_QUEUED_CONNECTIONS = 4;
    private static final int SOCKET_TIMEOUT_MILLIS = 60_000;

    private final String token;
    private final long maximumClipboardBytes;
    private final ClipboardStore clipboardStore;
    private final FileStore fileStore;
    private final Listener listener;
    private final String serverId = UUID.randomUUID().toString();
    private volatile boolean stopping;

    EmbeddedServer(
            Context context,
            int port,
            String token,
            long maximumClipboardBytes,
            long maximumFileBytes,
            Listener listener) {
        super(port);
        this.token = token;
        this.maximumClipboardBytes = maximumClipboardBytes;
        this.clipboardStore = new ClipboardStore(context);
        this.fileStore = new FileStore(context, maximumFileBytes);
        this.listener = listener;
        setAsyncRunner(new BoundedAsyncRunner());
    }

    void startServer() throws IOException {
        start(SOCKET_TIMEOUT_MILLIS, true);
    }

    void stopServer() {
        stopping = true;
        clipboardStore.closeStore();
        fileStore.closeStore();
        stop();
    }

    FileStore.Snapshot fileSnapshot() {
        return fileStore.snapshot();
    }

    @Override
    public Response serve(IHTTPSession session) {
        try {
            String uri = session.getUri();
            if (Method.GET.equals(session.getMethod()) && "/api/v1/health".equals(uri)) {
                JSONObject health = new JSONObject();
                health.put("ok", true);
                health.put("version", 1);
                health.put("serverId", serverId);
                health.put("fileMaxBytes", fileStoreMaximumBytes());
                health.put("capabilities", new org.json.JSONArray()
                        .put("clipboard-v1")
                        .put("files-v1"));
                return json(Response.Status.OK, health);
            }
            if (!authorized(session.getHeaders())) {
                Response response = error(Response.Status.UNAUTHORIZED, "认证失败");
                response.addHeader("WWW-Authenticate", "Bearer");
                return response;
            }
            if ("/api/v1/clipboard".equals(uri)) return serveClipboard(session);
            if ("/api/v1/files".equals(uri)) return serveFiles(session);
            if (uri.startsWith("/api/v1/files/")) {
                String id = uri.substring("/api/v1/files/".length());
                validatePathId(id);
                return serveFile(session, id);
            }
            return error(Response.Status.NOT_FOUND, "接口不存在");
        } catch (HttpFailure failure) {
            return error(failure.status, failure.getMessage());
        } catch (FileStore.NotFoundException exception) {
            return error(Response.Status.NOT_FOUND, exception.getMessage());
        } catch (FileStore.ConflictException exception) {
            return error(Response.Status.CONFLICT, exception.getMessage());
        } catch (FileStore.LimitExceededException exception) {
            return error(Response.Status.PAYLOAD_TOO_LARGE, exception.getMessage());
        } catch (FileStore.ProtocolException exception) {
            return error(Response.Status.BAD_REQUEST, exception.getMessage());
        } catch (JSONException exception) {
            return error(Response.Status.BAD_REQUEST, "JSON 数据无效");
        } catch (IOException exception) {
            Log.w(LOG_TAG, "Request I/O failed", exception);
            return error(Response.Status.INTERNAL_ERROR, "服务器存储操作失败");
        } catch (RuntimeException exception) {
            return error(Response.Status.INTERNAL_ERROR, "服务器处理请求失败");
        }
    }

    private Response serveClipboard(IHTTPSession session)
            throws IOException, JSONException, HttpFailure {
        if (Method.GET.equals(session.getMethod())) {
            long after = parseNonNegativeLong(session.getParms().get("after"), 0L, "after 参数无效");
            ClipboardStore.Snapshot snapshot = clipboardStore.latest(after);
            if (snapshot.item == null) {
                Response response = empty(Response.Status.NO_CONTENT);
                response.addHeader("X-FlowClip-Revision", Long.toString(snapshot.revision));
                return response;
            }
            JSONObject body = new JSONObject();
            body.put("revision", snapshot.revision);
            body.put("item", snapshot.item.toJson());
            return json(Response.Status.OK, body);
        }
        if (Method.POST.equals(session.getMethod())) {
            requireJson(session);
            long maximum = PayloadPolicy.maximumBase64Length(maximumClipboardBytes) + 16 * 1024L;
            byte[] body = readFixedBody(session, maximum);
            ClipItem item = ClipItem.fromJson(
                    new JSONObject(new String(body, StandardCharsets.UTF_8)),
                    maximumClipboardBytes);
            long revision = clipboardStore.put(item);
            JSONObject result = new JSONObject();
            result.put("ok", true);
            result.put("revision", revision);
            result.put("serverId", serverId);
            notifyClipboardReceived(item);
            return json(Response.Status.OK, result);
        }
        return methodNotAllowed("GET, POST");
    }

    private Response serveFiles(IHTTPSession session)
            throws IOException, JSONException, HttpFailure {
        if (Method.GET.equals(session.getMethod())) {
            return json(Response.Status.OK, fileStore.snapshot().toJson());
        }
        if (Method.POST.equals(session.getMethod())) {
            requireJson(session);
            byte[] body = readFixedBody(session, FileTransferPolicy.MAX_METADATA_BYTES);
            FileMetadata requested = FileMetadata.fromUploadJson(
                    new JSONObject(new String(body, StandardCharsets.UTF_8)),
                    Long.MAX_VALUE);
            if (requested.size > fileStoreMaximumBytes()) {
                throw new HttpFailure(
                        Response.Status.PAYLOAD_TOO_LARGE,
                        "文件超过大小限制");
            }
            FileStore.MutationResult reserved = fileStore.reserve(requested);
            JSONObject result = new JSONObject();
            result.put("ok", true);
            result.put("revision", reserved.revision);
            result.put("file", reserved.metadata.toJson());
            return json(Response.Status.CREATED, result);
        }
        return methodNotAllowed("GET, POST");
    }

    private Response serveFile(IHTTPSession session, String id)
            throws IOException, JSONException, HttpFailure {
        if (Method.PUT.equals(session.getMethod())) {
            rejectTransferEncoding(session);
            long length = contentLength(session, fileStoreMaximumBytes());
            FileStore.MutationResult completed = fileStore.put(
                    id, session.getInputStream(), length);
            FileStore.StoredFile received = fileStore.stored(id);
            FileStore.Snapshot snapshot = fileStore.snapshot();
            notifyFilesChanged(snapshot, received);
            JSONObject result = new JSONObject();
            result.put("ok", true);
            result.put("revision", completed.revision);
            result.put("file", completed.metadata.toJson());
            return json(Response.Status.OK, result);
        }
        if (Method.GET.equals(session.getMethod())) {
            FileStore.OpenedFile opened = fileStore.open(id);
            Response response = newFixedLengthResponse(
                    Response.Status.OK,
                    opened.stored.metadata.mime,
                    opened.input,
                    opened.stored.metadata.size);
            return commonHeaders(response);
        }
        if (Method.DELETE.equals(session.getMethod())) {
            long revision = fileStore.delete(id);
            JSONObject result = new JSONObject();
            result.put("ok", true);
            result.put("revision", revision);
            FileStore.Snapshot snapshot = fileStore.snapshot();
            notifyFilesChanged(snapshot, null);
            return json(Response.Status.OK, result);
        }
        return methodNotAllowed("GET, PUT, DELETE");
    }

    private long fileStoreMaximumBytes() {
        return fileStore.maximumBytes();
    }

    private void notifyClipboardReceived(ClipItem item) {
        if (listener == null || stopping) return;
        try {
            listener.onClipboardReceived(item);
        } catch (RuntimeException exception) {
            Log.w(LOG_TAG, "Clipboard listener failed", exception);
        }
    }

    private void notifyFilesChanged(FileStore.Snapshot snapshot, FileStore.StoredFile received) {
        if (listener == null || stopping) return;
        try {
            listener.onFilesChanged(snapshot, received);
        } catch (RuntimeException exception) {
            Log.w(LOG_TAG, "File listener failed", exception);
        }
    }

    private boolean authorized(Map<String, String> headers) {
        String provided = headers.get("authorization");
        if (provided == null) return false;
        byte[] actual = provided.getBytes(StandardCharsets.UTF_8);
        byte[] expected = ("Bearer " + token).getBytes(StandardCharsets.US_ASCII);
        return MessageDigest.isEqual(actual, expected);
    }

    private static void requireJson(IHTTPSession session) throws HttpFailure {
        String contentType = session.getHeaders().get("content-type");
        if (contentType == null
                || !"application/json".equalsIgnoreCase(contentType.split(";", 2)[0].trim())) {
            throw new HttpFailure(Response.Status.UNSUPPORTED_MEDIA_TYPE, "仅接受 application/json");
        }
    }

    private static byte[] readFixedBody(IHTTPSession session, long maximum)
            throws IOException, HttpFailure {
        rejectTransferEncoding(session);
        long length = contentLength(session, maximum);
        if (length > Integer.MAX_VALUE) {
            throw new HttpFailure(Response.Status.PAYLOAD_TOO_LARGE, "请求体超过大小限制");
        }
        ByteArrayOutputStream output = new ByteArrayOutputStream((int) length);
        InputStream input = session.getInputStream();
        byte[] buffer = new byte[16 * 1024];
        long remaining = length;
        while (remaining > 0L) {
            int read = input.read(buffer, 0, (int) Math.min((long) buffer.length, remaining));
            if (read < 0) throw new HttpFailure(Response.Status.BAD_REQUEST, "请求体提前结束");
            if (read == 0) continue;
            output.write(buffer, 0, read);
            remaining -= read;
        }
        return output.toByteArray();
    }

    private static long contentLength(IHTTPSession session, long maximum) throws HttpFailure {
        String raw = session.getHeaders().get("content-length");
        if (raw == null || raw.trim().isEmpty()) {
            throw new HttpFailure(Response.Status.LENGTH_REQUIRED, "请求必须包含 Content-Length");
        }
        long value;
        try {
            value = Long.parseLong(raw.trim());
        } catch (NumberFormatException exception) {
            throw new HttpFailure(Response.Status.BAD_REQUEST, "Content-Length 无效");
        }
        if (value < 0L) throw new HttpFailure(Response.Status.BAD_REQUEST, "Content-Length 无效");
        if (value > maximum) {
            throw new HttpFailure(Response.Status.PAYLOAD_TOO_LARGE, "请求体超过大小限制");
        }
        return value;
    }

    private static void rejectTransferEncoding(IHTTPSession session) throws HttpFailure {
        String value = session.getHeaders().get("transfer-encoding");
        if (value != null && !value.trim().isEmpty()) {
            throw new HttpFailure(Response.Status.BAD_REQUEST, "不支持 Transfer-Encoding");
        }
    }

    private static long parseNonNegativeLong(
            String raw, long fallback, String message) throws HttpFailure {
        if (raw == null) return fallback;
        try {
            long value = Long.parseLong(raw);
            if (value < 0L) throw new NumberFormatException();
            return value;
        } catch (NumberFormatException exception) {
            throw new HttpFailure(Response.Status.BAD_REQUEST, message);
        }
    }

    private static void validatePathId(String id) throws HttpFailure {
        try {
            if (!UUID.fromString(id).toString().equals(id)) {
                throw new IllegalArgumentException();
            }
        } catch (IllegalArgumentException exception) {
            throw new HttpFailure(Response.Status.BAD_REQUEST, "文件 ID 无效");
        }
    }

    private static Response json(Response.IStatus status, JSONObject payload) {
        byte[] encoded = payload.toString().getBytes(StandardCharsets.UTF_8);
        Response response = newFixedLengthResponse(
                status,
                "application/json; charset=utf-8",
                new ByteArrayInputStream(encoded),
                encoded.length);
        return commonHeaders(response);
    }

    private static Response error(Response.IStatus status, String message) {
        try {
            return json(status, new JSONObject().put("error", message));
        } catch (JSONException impossible) {
            throw new AssertionError(impossible);
        }
    }

    private static Response empty(Response.IStatus status) {
        return commonHeaders(newFixedLengthResponse(status, null, ""));
    }

    private static Response methodNotAllowed(String allowed) {
        Response response = error(Response.Status.METHOD_NOT_ALLOWED, "请求方法不受支持");
        response.addHeader("Allow", allowed);
        return response;
    }

    private static Response commonHeaders(Response response) {
        response.addHeader("Cache-Control", "no-store");
        response.addHeader("Connection", "close");
        response.addHeader("X-Content-Type-Options", "nosniff");
        return response;
    }

    private final class BoundedAsyncRunner implements AsyncRunner {
        private final Set<ClientHandler> running = Collections.newSetFromMap(
                new ConcurrentHashMap<>());
        private final ThreadPoolExecutor executor = new ThreadPoolExecutor(
                MAX_WORKERS,
                MAX_WORKERS,
                30L,
                TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(MAX_QUEUED_CONNECTIONS),
                runnable -> {
                    Thread thread = new Thread(runnable, "flowclip-http");
                    thread.setDaemon(true);
                    return thread;
                });

        BoundedAsyncRunner() {
            executor.allowCoreThreadTimeOut(true);
        }

        @Override
        public void closeAll() {
            for (ClientHandler handler : running) closeQuietly(handler);
            running.clear();
            executor.shutdownNow();
            try {
                executor.awaitTermination(500L, TimeUnit.MILLISECONDS);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
        }

        @Override
        public void closed(ClientHandler handler) {
            running.remove(handler);
        }

        @Override
        public void exec(ClientHandler handler) {
            running.add(handler);
            try {
                executor.execute(handler);
            } catch (RejectedExecutionException exception) {
                running.remove(handler);
                closeQuietly(handler);
            }
        }

        private void closeQuietly(ClientHandler handler) {
            try {
                handler.close();
            } catch (RuntimeException ignored) {
                // The connection may already be closed by NanoHTTPD.
            }
        }
    }

    private static final class ClipboardStore {
        private static final String PREFS = "flowclip_clipboard_server_v1";
        private static final String KEY_REVISION = "revision";

        private final SharedPreferences preferences;
        private long revision;
        private ClipItem item;
        private boolean closed;

        ClipboardStore(Context context) {
            preferences = context.getApplicationContext()
                    .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
            revision = Math.max(0L, preferences.getLong(KEY_REVISION, 0L));
        }

        synchronized long put(ClipItem value) throws IOException {
            if (closed) throw new IOException("手机服务器正在停止");
            if (item != null && item.id.equals(value.id)) return revision;
            long next = revision == Long.MAX_VALUE ? 1L : revision + 1L;
            if (!preferences.edit().putLong(KEY_REVISION, next).commit()) {
                throw new IOException("无法保存剪贴板修订号");
            }
            revision = next;
            item = value;
            return revision;
        }

        synchronized void closeStore() {
            closed = true;
        }

        synchronized Snapshot latest(long after) {
            return new Snapshot(revision, item == null || after == revision ? null : item);
        }

        static final class Snapshot {
            final long revision;
            final ClipItem item;

            Snapshot(long revision, ClipItem item) {
                this.revision = revision;
                this.item = item;
            }
        }
    }

    private static final class HttpFailure extends Exception {
        final Response.IStatus status;

        HttpFailure(Response.IStatus status, String message) {
            super(message);
            this.status = status;
        }
    }
}
