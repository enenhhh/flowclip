package com.flowclip.app;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.IOException;

final class SyncCursor {
    static final Object TRANSACTION_LOCK = new Object();
    private static final String PREFS = "flowclip_cursors";

    private SyncCursor() {}

    static long load(Context context, AppConfig config) {
        return Math.max(0L, context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getLong(key(config), 0L));
    }

    static void save(Context context, AppConfig config, long revision) throws IOException {
        if (revision < 0L) throw new IOException("服务器修订号无效");
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String cursorKey = key(config);
        long previous = Math.max(0L, preferences.getLong(cursorKey, 0L));
        boolean saved = preferences.edit()
                .putLong(cursorKey, revision)
                .commit();
        if (!saved) {
            // commit() mutates the in-memory map before reporting a disk failure.
            boolean restored = preferences.edit().putLong(cursorKey, previous).commit();
            throw new IOException(restored
                    ? "无法保存同步进度"
                    : "无法保存同步进度，恢复旧进度写盘也失败");
        }
    }

    private static String key(AppConfig config) {
        return "revision_" + config.endpointKey();
    }
}
