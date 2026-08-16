package com.flowclip.app;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.Locale;
import java.util.UUID;

final class ClipItem {
    final String id;
    final String origin;
    final String kind;
    final String mime;
    final String filename;
    final byte[] data;
    final long createdAt;

    ClipItem(
            String id,
            String origin,
            String kind,
            String mime,
            String filename,
            byte[] data,
            long createdAt) {
        this.id = id;
        this.origin = origin;
        this.kind = kind;
        this.mime = mime;
        this.filename = filename;
        this.data = data;
        this.createdAt = createdAt;
    }

    static ClipItem text(String origin, String text) {
        if (text.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("文本不能包含 NUL 字符");
        }
        return new ClipItem(
                UUID.randomUUID().toString(), origin, "text", "text/plain; charset=utf-8", "",
                text.getBytes(StandardCharsets.UTF_8), System.currentTimeMillis());
    }

    static ClipItem image(String origin, String mime, String filename, byte[] data) {
        validateMime(mime, "image");
        return new ClipItem(
                UUID.randomUUID().toString(), origin, "image", mime, filename, data,
                System.currentTimeMillis());
    }

    JSONObject toJson() throws JSONException {
        JSONObject result = new JSONObject();
        result.put("id", id);
        result.put("origin", origin);
        result.put("kind", kind);
        result.put("mime", mime);
        result.put("filename", filename);
        result.put("data", Base64.getEncoder().encodeToString(data));
        result.put("sha256", sha256(data));
        result.put("createdAt", createdAt);
        return result;
    }

    static ClipItem fromJson(JSONObject raw, long maxBytes) throws JSONException {
        String id = required(raw, "id", 128);
        String origin = required(raw, "origin", 128);
        String kind = required(raw, "kind", 16);
        String mime = required(raw, "mime", 128).toLowerCase(Locale.ROOT);
        String filename = "";
        if (raw.has("filename")) {
            Object rawFilename = raw.opt("filename");
            if (!(rawFilename instanceof String)) {
                throw new JSONException("字段 filename 无效");
            }
            filename = (String) rawFilename;
        }
        if (filename.length() > 255) {
            throw new JSONException("文件名过长");
        }
        if (!("text".equals(kind) || "image".equals(kind))) {
            throw new JSONException("不支持的剪贴板类型");
        }
        try {
            validateMime(mime, kind);
        } catch (IllegalArgumentException exception) {
            throw new JSONException(exception.getMessage());
        }
        String encoded;
        byte[] data;
        try {
            encoded = required(raw, "data", Integer.MAX_VALUE);
            long maximumEncodedLength = PayloadPolicy.maximumBase64Length(maxBytes);
            if (encoded.length() > maximumEncodedLength) {
                throw new JSONException("内容超过大小限制");
            }
            data = Base64.getDecoder().decode(encoded);
        } catch (IllegalArgumentException exception) {
            throw new JSONException("Base64 数据无效");
        }
        if (!Base64.getEncoder().encodeToString(data).equals(encoded)) {
            throw new JSONException("Base64 数据必须使用规范的带填充格式");
        }
        if (data.length > maxBytes) {
            throw new JSONException("内容超过大小限制");
        }
        String expected = required(raw, "sha256", 64).toLowerCase(Locale.ROOT);
        if (!expected.equals(sha256(data))) {
            throw new JSONException("内容校验失败");
        }
        long createdAt = raw.optLong("createdAt", -1);
        if (createdAt < 0) {
            throw new JSONException("时间字段无效");
        }
        if ("text".equals(kind)) {
            String roundTrip = new String(data, StandardCharsets.UTF_8);
            if (!java.util.Arrays.equals(roundTrip.getBytes(StandardCharsets.UTF_8), data)) {
                throw new JSONException("文本不是有效 UTF-8");
            }
            if (roundTrip.indexOf('\0') >= 0) {
                throw new JSONException("文本不能包含 NUL 字符");
            }
        }
        return new ClipItem(id, origin, kind, mime, filename, data, createdAt);
    }

    String signature() {
        MessageDigest digest = newDigest();
        digest.update(kind.getBytes(StandardCharsets.US_ASCII));
        digest.update((byte) 0);
        digest.update(mime.getBytes(StandardCharsets.US_ASCII));
        digest.update((byte) 0);
        digest.update(data);
        return hex(digest.digest());
    }

    String text() {
        return new String(data, StandardCharsets.UTF_8);
    }

    static void validateMime(String mime, String expectedKind) {
        if (mime == null || mime.length() > 128 || !isAscii(mime)) {
            throw new IllegalArgumentException("MIME 类型无效");
        }
        String normalized = mime.toLowerCase(Locale.ROOT);
        String[] sections = normalized.split(";", -1);
        String mediaType = sections[0];
        int slash = mediaType.indexOf('/');
        if (!mediaType.equals(mediaType.trim()) || slash <= 0
                || slash != mediaType.lastIndexOf('/') || slash == mediaType.length() - 1) {
            throw new IllegalArgumentException("MIME 类型无效");
        }
        String major = mediaType.substring(0, slash);
        String subtype = mediaType.substring(slash + 1);
        if (!isMimeToken(major, true) || !isMimeToken(subtype, true)) {
            throw new IllegalArgumentException("MIME 类型无效");
        }
        if (!major.equals(expectedKind)) {
            throw new IllegalArgumentException(
                    "text".equals(expectedKind) ? "文本 MIME 类型无效" : "图片 MIME 类型无效");
        }
        for (int index = 1; index < sections.length; index++) {
            String parameter = sections[index].trim();
            int equals = parameter.indexOf('=');
            if (equals <= 0 || equals != parameter.lastIndexOf('=')
                    || equals == parameter.length() - 1
                    || !isMimeToken(parameter.substring(0, equals), false)
                    || !isMimeToken(parameter.substring(equals + 1), false)) {
                throw new IllegalArgumentException("MIME 参数无效");
            }
        }
    }

    private static boolean isMimeToken(String value, boolean requireAlnumStart) {
        if (value.isEmpty()) return false;
        if (requireAlnumStart && !isAsciiAlnum(value.charAt(0))) return false;
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (!isAsciiAlnum(character) && "!#$&^_.+-".indexOf(character) < 0) return false;
        }
        return true;
    }

    private static boolean isAsciiAlnum(char value) {
        return (value >= 'a' && value <= 'z') || (value >= '0' && value <= '9');
    }

    private static boolean isAscii(String value) {
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character < 0x20 || character > 0x7e) return false;
        }
        return true;
    }

    private static String required(JSONObject raw, String key, int maxLength) throws JSONException {
        Object value = raw.opt(key);
        if (!(value instanceof String)) {
            throw new JSONException("字段 " + key + " 无效");
        }
        String text = (String) value;
        if (text.isEmpty() || text.length() > maxLength) {
            throw new JSONException("字段 " + key + " 无效");
        }
        return text;
    }

    private static String sha256(byte[] data) {
        MessageDigest digest = newDigest();
        return hex(digest.digest(data));
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
}
