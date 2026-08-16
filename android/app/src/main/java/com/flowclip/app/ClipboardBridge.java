package com.flowclip.app;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.Context;
import android.net.Uri;
import android.os.PersistableBundle;
import android.provider.OpenableColumns;

import java.io.IOException;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

final class ClipboardBridge {
    private ClipboardBridge() {}

    static ClipItem capture(Context context, AppConfig config) throws IOException {
        ClipboardManager manager = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
        if (manager == null || !manager.hasPrimaryClip()) {
            return null;
        }
        ClipData clip = manager.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) {
            return null;
        }
        ClipData.Item first = clip.getItemAt(0);
        Uri uri = first.getUri();
        ContentResolver resolver = context.getContentResolver();
        if (uri != null) {
            String mime = resolver.getType(uri);
            if (mime != null && mime.toLowerCase(Locale.ROOT).startsWith("image/")) {
                try (InputStream input = resolver.openInputStream(uri)) {
                    if (input == null) {
                        throw new IOException("无法读取剪贴板中的图片");
                    }
                    byte[] data = ApiClient.readAll(input, config.maxBytes());
                    return ClipItem.image(
                            config.deviceId, mime, queryName(resolver, uri, mime), data);
                } catch (SecurityException exception) {
                    throw new IOException("没有权限读取剪贴板中的图片", exception);
                }
            }
            return null;
        }
        CharSequence text = first.getText();
        if (text == null || text.length() == 0) {
            return null;
        }
        ClipItem item = ClipItem.text(config.deviceId, text.toString());
        if (item.data.length > config.maxBytes()) {
            throw new IOException("文本超过大小限制");
        }
        return item;
    }

    static void applyText(Context context, ClipItem item) {
        ClipboardManager manager = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
        if (manager != null) {
            ClipData clip = ClipData.newPlainText("FlowClip", item.text());
            PersistableBundle extras = new PersistableBundle();
            extras.putBoolean("android.content.extra.IS_SENSITIVE", true);
            clip.getDescription().setExtras(extras);
            manager.setPrimaryClip(clip);
        }
    }

    static String queryName(ContentResolver resolver, Uri uri, String mime) {
        try (android.database.Cursor cursor = resolver.query(
                uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                String value = cursor.getString(0);
                if (value != null && !value.trim().isEmpty()) {
                    return sanitizeName(value);
                }
            }
        } catch (RuntimeException ignored) {
            // Fall back to a generated file name.
        }
        String extension = extensionForMime(mime);
        return "FlowClip_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
                .format(new Date()) + extension;
    }

    static String sanitizeName(String value) {
        String result = value.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_").trim();
        if (result.isEmpty()) {
            return "FlowClip_image";
        }
        return result.length() > 180 ? result.substring(result.length() - 180) : result;
    }

    static String extensionForMime(String mime) {
        String lower = mime == null ? "" : mime.toLowerCase(Locale.ROOT);
        if (lower.contains("png")) return ".png";
        if (lower.contains("webp")) return ".webp";
        if (lower.contains("gif")) return ".gif";
        if (lower.contains("bmp")) return ".bmp";
        if (lower.contains("heic") || lower.contains("heif")) return ".heic";
        return ".jpg";
    }
}
