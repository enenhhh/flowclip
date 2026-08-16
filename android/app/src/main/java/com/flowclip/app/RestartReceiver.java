package com.flowclip.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class RestartReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? null : intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
                && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            return;
        }
        AppConfig config = AppConfig.load(context);
        if (!config.serviceRequired() || !config.isConfigured()) return;

        Intent service = new Intent(context, SyncService.class)
                .setAction(SyncService.ACTION_START);
        try {
            context.startForegroundService(service);
        } catch (RuntimeException exception) {
            context.getSharedPreferences("flowclip_runtime", Context.MODE_PRIVATE).edit()
                    .putString("level", "warning")
                    .putString("message", "系统暂未允许恢复后台服务，请打开 FlowClip")
                    .apply();
        }
    }
}
