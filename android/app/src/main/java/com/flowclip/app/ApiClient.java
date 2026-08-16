package com.flowclip.app;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class ApiClient {
    private final AppConfig config;

    ApiClient(AppConfig config) {
        config.validate();
        this.config = config;
    }

    JSONObject health() throws IOException, JSONException {
        Response response = request("GET", "/api/v1/health", null, false);
        if (response.code != HttpURLConnection.HTTP_OK) {
            throw failure(response);
        }
        return new JSONObject(new String(response.body, StandardCharsets.UTF_8));
    }

    FetchResult fetch(long after) throws IOException, JSONException {
        Response response = request(
                "GET", "/api/v1/clipboard?after=" + Math.max(0, after), null, true);
        if (response.code == HttpURLConnection.HTTP_NO_CONTENT) {
            long revision = after;
            if (response.revisionHeader != null) {
                try {
                    revision = Long.parseLong(response.revisionHeader);
                } catch (NumberFormatException ignored) {
                    // Keep the known revision if the optional response header is malformed.
                }
            }
            return new FetchResult(revision, null);
        }
        if (response.code != HttpURLConnection.HTTP_OK) {
            throw failure(response);
        }
        JSONObject raw = new JSONObject(new String(response.body, StandardCharsets.UTF_8));
        long revision = raw.getLong("revision");
        ClipItem item = ClipItem.fromJson(raw.getJSONObject("item"), config.maxBytes());
        return new FetchResult(revision, item);
    }

    long push(ClipItem item) throws IOException, JSONException {
        if (item.data.length > config.maxBytes()) {
            throw new IOException("内容超过大小限制");
        }
        byte[] body = item.toJson().toString().getBytes(StandardCharsets.UTF_8);
        Response response = request("POST", "/api/v1/clipboard", body, true);
        if (response.code != HttpURLConnection.HTTP_OK) {
            throw failure(response);
        }
        JSONObject raw = new JSONObject(new String(response.body, StandardCharsets.UTF_8));
        return raw.getLong("revision");
    }

    private Response request(
            String method, String path, byte[] body, boolean authenticated) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(config.effectiveServerUrl() + path)
                .openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(15_000);
        connection.setUseCaches(false);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("User-Agent", "FlowClip-Android/1.4.0");
        if (authenticated) {
            connection.setRequestProperty("Authorization", "Bearer " + config.token);
        }
        if (body != null) {
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(body.length);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body);
            }
        }
        try {
            int code = connection.getResponseCode();
            InputStream stream = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
            long encodedLimit = PayloadPolicy.maximumBase64Length(config.maxBytes());
            byte[] responseBody = stream == null ? new byte[0]
                    : readAll(stream, encodedLimit + 32_768L);
            return new Response(
                    code, responseBody, connection.getHeaderField("X-FlowClip-Revision"));
        } finally {
            connection.disconnect();
        }
    }

    private static IOException failure(Response response) {
        String message = "服务器返回 HTTP " + response.code;
        if (response.body.length > 0) {
            try {
                String detail = new JSONObject(new String(response.body, StandardCharsets.UTF_8))
                        .optString("error", "");
                if (!detail.isEmpty()) {
                    message = detail;
                }
            } catch (JSONException ignored) {
                // Use the status-code message.
            }
        }
        return new IOException(message);
    }

    static byte[] readAll(InputStream input, long maximum) throws IOException {
        try (InputStream source = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[16 * 1024];
            long total = 0;
            int read;
            while ((read = source.read(buffer)) != -1) {
                total += read;
                if (total > maximum) {
                    throw new IOException("内容超过大小限制");
                }
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        }
    }

    static final class FetchResult {
        final long revision;
        final ClipItem item;

        FetchResult(long revision, ClipItem item) {
            this.revision = revision;
            this.item = item;
        }
    }

    private static final class Response {
        final int code;
        final byte[] body;
        final String revisionHeader;

        Response(int code, byte[] body, String revisionHeader) {
            this.code = code;
            this.body = body;
            this.revisionHeader = revisionHeader;
        }
    }
}
