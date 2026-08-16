package com.flowclip.app;

import org.json.JSONException;
import org.json.JSONObject;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.util.Locale;
import java.util.UUID;

final class FileMetadata {
    private static final int MAX_FILENAME_UTF8_BYTES = 180;
    private static final int MAX_FILENAME_UTF16_UNITS = 255;

    final String id;
    final String origin;
    final String filename;
    final String mime;
    final long size;
    final long createdAt;
    final String status;
    final String sha256;

    FileMetadata(
            String id,
            String origin,
            String filename,
            String mime,
            long size,
            long createdAt,
            String status,
            String sha256) {
        this.id = validateId(id);
        this.origin = validateOrigin(origin);
        this.filename = sanitizeFilename(filename);
        this.mime = validateMime(mime);
        if (size < 0L) throw new IllegalArgumentException("文件大小无效");
        if (createdAt < 0L) throw new IllegalArgumentException("文件时间无效");
        if (!"pending".equals(status) && !"ready".equals(status)) {
            throw new IllegalArgumentException("文件状态无效");
        }
        if ("pending".equals(status) && sha256 != null) {
            throw new IllegalArgumentException("待上传文件不能包含校验值");
        }
        this.size = size;
        this.createdAt = createdAt;
        this.status = status;
        this.sha256 = "ready".equals(status) ? validateSha256(sha256) : null;
    }

    static FileMetadata createUpload(
            String origin, String filename, String mime, long size) {
        return new FileMetadata(
                UUID.randomUUID().toString(),
                origin,
                filename,
                mime,
                size,
                System.currentTimeMillis(),
                "pending",
                null);
    }

    static FileMetadata fromUploadJson(JSONObject raw, long maximumBytes)
            throws JSONException {
        try {
            FileMetadata metadata = new FileMetadata(
                    requiredString(raw, "id", 36),
                    requiredString(raw, "origin", 256),
                    requiredString(raw, "filename", 1024),
                    requiredString(raw, "mime", 128),
                    requiredLong(raw, "size"),
                    requiredLong(raw, "createdAt"),
                    "pending",
                    null);
            if (metadata.size > maximumBytes) {
                throw new JSONException("文件超过大小限制");
            }
            return metadata;
        } catch (IllegalArgumentException exception) {
            throw new JSONException(exception.getMessage());
        }
    }

    static FileMetadata fromJson(JSONObject raw, long maximumBytes) throws JSONException {
        try {
            String status = requiredString(raw, "status", 7);
            Object checksum = raw.opt("sha256");
            FileMetadata metadata = new FileMetadata(
                    requiredString(raw, "id", 36),
                    requiredString(raw, "origin", 256),
                    requiredString(raw, "filename", 1024),
                    requiredString(raw, "mime", 128),
                    requiredLong(raw, "size"),
                    requiredLong(raw, "createdAt"),
                    status,
                    checksum instanceof String ? (String) checksum : null);
            if (metadata.size > maximumBytes) {
                throw new JSONException("文件超过大小限制");
            }
            return metadata;
        } catch (IllegalArgumentException exception) {
            throw new JSONException(exception.getMessage());
        }
    }

    FileMetadata completed(String checksum) {
        return new FileMetadata(
                id, origin, filename, mime, size, createdAt, "ready", checksum);
    }

    JSONObject toUploadJson() throws JSONException {
        JSONObject result = new JSONObject();
        result.put("id", id);
        result.put("origin", origin);
        result.put("filename", filename);
        result.put("mime", mime);
        result.put("size", size);
        result.put("createdAt", createdAt);
        return result;
    }

    JSONObject toJson() throws JSONException {
        JSONObject result = toUploadJson();
        result.put("status", status);
        if (sha256 != null) result.put("sha256", sha256);
        return result;
    }

    boolean sameUpload(FileMetadata other) {
        return other != null
                && id.equals(other.id)
                && origin.equals(other.origin)
                && filename.equals(other.filename)
                && mime.equals(other.mime)
                && size == other.size
                && createdAt == other.createdAt;
    }

    static String sanitizeFilename(String value) {
        if (value == null || hasUnpairedSurrogate(value)) {
            throw new IllegalArgumentException("文件名无效");
        }
        String normalized = Normalizer.normalize(value, Normalizer.Form.NFC);
        if (normalized.isEmpty() || ".".equals(normalized) || "..".equals(normalized)) {
            throw new IllegalArgumentException("文件名无效");
        }
        if (normalized.length() > MAX_FILENAME_UTF16_UNITS
                || normalized.getBytes(StandardCharsets.UTF_8).length > MAX_FILENAME_UTF8_BYTES) {
            throw new IllegalArgumentException("文件名超过长度限制");
        }
        for (int offset = 0; offset < normalized.length();) {
            int codePoint = normalized.codePointAt(offset);
            offset += Character.charCount(codePoint);
            if ("<>:\"/\\|?*".indexOf(codePoint) >= 0
                    || Character.getType(codePoint) == Character.CONTROL) {
                throw new IllegalArgumentException("文件名包含系统保留字符");
            }
        }
        if (normalized.endsWith(" ") || normalized.endsWith(".")) {
            throw new IllegalArgumentException("文件名不能以空格或点结尾");
        }
        String stem = normalized.split("\\.", 2)[0].toUpperCase(Locale.ROOT);
        if (isWindowsDeviceName(stem)) {
            throw new IllegalArgumentException("文件名使用了系统保留设备名");
        }
        return normalized;
    }

    static String validateMime(String value) {
        if (value == null || value.isEmpty() || value.length() > 128) {
            throw new IllegalArgumentException("文件 MIME 类型无效");
        }
        int slash = value.indexOf('/');
        if (slash <= 0 || slash != value.lastIndexOf('/') || slash == value.length() - 1
                || !isMimeToken(value.substring(0, slash))
                || !isMimeToken(value.substring(slash + 1))) {
            throw new IllegalArgumentException("文件 MIME 类型无效");
        }
        return value.toLowerCase(Locale.ROOT);
    }

    private static boolean isMimeToken(String value) {
        if (value.isEmpty() || !isAsciiLetterOrDigit(value.charAt(0))) return false;
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            boolean allowed = isAsciiLetterOrDigit(character)
                    || "!#$&^_.+-".indexOf(character) >= 0;
            if (!allowed) return false;
        }
        return true;
    }

    private static boolean isAsciiLetterOrDigit(char value) {
        return value >= 'a' && value <= 'z'
                || value >= 'A' && value <= 'Z'
                || value >= '0' && value <= '9';
    }

    private static boolean isWindowsDeviceName(String stem) {
        if ("CON".equals(stem) || "PRN".equals(stem)
                || "AUX".equals(stem) || "NUL".equals(stem)) {
            return true;
        }
        if (stem.length() != 4
                || !(stem.startsWith("COM") || stem.startsWith("LPT"))) {
            return false;
        }
        char suffix = stem.charAt(3);
        return suffix >= '1' && suffix <= '9'
                || suffix == '\u00b9' || suffix == '\u00b2' || suffix == '\u00b3';
    }

    private static String validateId(String value) {
        if (value == null || value.length() != 36) {
            throw new IllegalArgumentException("文件 ID 必须是规范 UUID");
        }
        try {
            UUID parsed = UUID.fromString(value);
            if (!parsed.toString().equals(value)) {
                throw new IllegalArgumentException("文件 ID 必须是规范 UUID");
            }
            return value;
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("文件 ID 必须是规范 UUID");
        }
    }

    private static String validateOrigin(String value) {
        if (value == null || value.isEmpty()
                || value.codePointCount(0, value.length()) > 128
                || hasUnpairedSurrogate(value)) {
            throw new IllegalArgumentException("文件来源无效");
        }
        for (int offset = 0; offset < value.length();) {
            int codePoint = value.codePointAt(offset);
            offset += Character.charCount(codePoint);
            if (Character.getType(codePoint) == Character.CONTROL) {
                throw new IllegalArgumentException("文件来源无效");
            }
        }
        return value;
    }

    private static String validateSha256(String value) {
        if (value == null) throw new IllegalArgumentException("文件校验值无效");
        String normalized = value.toLowerCase(Locale.ROOT);
        if (normalized.length() != 64) throw new IllegalArgumentException("文件校验值无效");
        for (int index = 0; index < normalized.length(); index++) {
            char character = normalized.charAt(index);
            if (!(character >= '0' && character <= '9'
                    || character >= 'a' && character <= 'f')) {
                throw new IllegalArgumentException("文件校验值无效");
            }
        }
        return normalized;
    }

    private static boolean hasUnpairedSurrogate(String value) {
        for (int index = 0; index < value.length(); index++) {
            char current = value.charAt(index);
            if (Character.isHighSurrogate(current)) {
                if (index + 1 >= value.length()
                        || !Character.isLowSurrogate(value.charAt(index + 1))) {
                    return true;
                }
                index += 1;
            } else if (Character.isLowSurrogate(current)) {
                return true;
            }
        }
        return false;
    }

    private static String requiredString(JSONObject raw, String key, int maximum)
            throws JSONException {
        Object value = raw.opt(key);
        if (!(value instanceof String)) throw new JSONException("字段 " + key + " 无效");
        String text = (String) value;
        if (text.isEmpty() || text.length() > maximum) {
            throw new JSONException("字段 " + key + " 无效");
        }
        return text;
    }

    private static long requiredLong(JSONObject raw, String key) throws JSONException {
        Object value = raw.opt(key);
        if (!(value instanceof Number)) throw new JSONException("字段 " + key + " 无效");
        try {
            return new BigDecimal(value.toString()).longValueExact();
        } catch (NumberFormatException | ArithmeticException exception) {
            throw new JSONException("字段 " + key + " 无效");
        }
    }
}
