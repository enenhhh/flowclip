package com.flowclip.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;

import java.security.SecureRandom;
import java.util.Base64;
import java.util.UUID;

final class AppConfig {
    private static final String PREFS = "flowclip_settings";

    final String deviceId;
    final String deviceName;
    final String serverUrl;
    final String token;
    final boolean allowInsecureLan;
    final boolean requireHttpsForPublic;
    final boolean autoReceive;
    final float pollIntervalSeconds;
    final int maxSizeMb;
    final boolean serverEnabled;
    final int listenPort;
    final int fileMaxMb;

    AppConfig(
            String deviceId,
            String deviceName,
            String serverUrl,
            String token,
            boolean allowInsecureLan,
            boolean requireHttpsForPublic,
            boolean autoReceive,
            float pollIntervalSeconds,
            int maxSizeMb,
            boolean serverEnabled,
            int listenPort,
            int fileMaxMb) {
        this.deviceId = deviceId;
        this.deviceName = deviceName;
        this.serverUrl = stripTrailingSlash(serverUrl);
        this.token = token;
        this.allowInsecureLan = allowInsecureLan;
        this.requireHttpsForPublic = requireHttpsForPublic;
        this.autoReceive = autoReceive;
        this.pollIntervalSeconds = pollIntervalSeconds;
        this.maxSizeMb = maxSizeMb;
        this.serverEnabled = serverEnabled;
        this.listenPort = listenPort;
        this.fileMaxMb = fileMaxMb;
    }

    static AppConfig load(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String id = prefs.getString("deviceId", "");
        if (id == null || id.isEmpty()) {
            id = UUID.randomUUID().toString();
            prefs.edit().putString("deviceId", id).apply();
        }
        String defaultName = Build.MODEL == null || Build.MODEL.isEmpty() ? "Android" : Build.MODEL;
        return new AppConfig(
                id,
                prefs.getString("deviceName", defaultName),
                prefs.getString("serverUrl", "http://192.168.1.10:8765"),
                prefs.getString("token", ""),
                prefs.getBoolean("allowInsecureLan", false),
                prefs.getBoolean("requireHttpsForPublic", true),
                prefs.getBoolean("autoReceive", true),
                prefs.getFloat("pollInterval", 2.0f),
                Math.min(PayloadPolicy.MAX_MEGABYTES,
                        prefs.getInt("maxSizeMb", PayloadPolicy.DEFAULT_MEGABYTES)),
                prefs.getBoolean("serverEnabled", false),
                prefs.getInt("listenPort", 8765),
                Math.min(FileTransferPolicy.MAX_MEGABYTES,
                        prefs.getInt("fileMaxMb", FileTransferPolicy.DEFAULT_MEGABYTES)));
    }

    void save(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString("deviceId", deviceId)
                .putString("deviceName", deviceName)
                .putString("serverUrl", serverUrl)
                .putString("token", token)
                .putBoolean("allowInsecureLan", allowInsecureLan)
                .putBoolean("requireHttpsForPublic", requireHttpsForPublic)
                .putBoolean("autoReceive", autoReceive)
                .putFloat("pollInterval", pollIntervalSeconds)
                .putInt("maxSizeMb", maxSizeMb)
                .putBoolean("serverEnabled", serverEnabled)
                .putInt("listenPort", listenPort)
                .putInt("fileMaxMb", fileMaxMb)
                .remove("autoSend")
                .remove("autoCopyText")
                .remove("startOnBoot")
                .apply();
    }

    static void disableBackgroundFeatures(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putBoolean("autoReceive", false)
                .putBoolean("serverEnabled", false)
                .apply();
    }

    void validate() {
        if (deviceName == null || deviceName.trim().isEmpty() || deviceName.length() > 64) {
            throw new IllegalArgumentException("设备名称应为 1 到 64 个字符");
        }
        TokenPolicy.validate(token);
        if (pollIntervalSeconds < 0.5f || pollIntervalSeconds > 60f) {
            throw new IllegalArgumentException("同步间隔应为 0.5 到 60 秒");
        }
        PayloadPolicy.validateMegabytes(maxSizeMb);
        FileTransferPolicy.validateMegabytes(fileMaxMb);
        if (listenPort < 1024 || listenPort > 65535) {
            throw new IllegalArgumentException("手机服务器端口应为 1024 到 65535");
        }
        if (!serverEnabled) {
            EndpointPolicy.validate(serverUrl, allowInsecureLan, requireHttpsForPublic);
        }
    }

    boolean isConfigured() {
        try {
            validate();
            return true;
        } catch (IllegalArgumentException ignored) {
            return false;
        }
    }

    long maxBytes() {
        return PayloadPolicy.bytes(maxSizeMb);
    }

    long fileMaxBytes() {
        return FileTransferPolicy.bytes(fileMaxMb);
    }

    String effectiveServerUrl() {
        return serverEnabled ? "http://127.0.0.1:" + listenPort : serverUrl;
    }

    boolean serviceRequired() {
        return autoReceive || serverEnabled;
    }

    String endpointKey() {
        return EndpointPolicy.endpointKey(effectiveServerUrl(), token);
    }

    static String generateToken() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static String stripTrailingSlash(String value) {
        String result = value == null ? "" : value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }
}
