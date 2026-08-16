package com.flowclip.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ClipData;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.Uri;
import android.os.IBinder;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import org.json.JSONException;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

public final class SyncService extends Service {
    static final String ACTION_START = "com.flowclip.app.START";
    static final String ACTION_STOP = "com.flowclip.app.STOP";
    static final String ACTION_STATUS = "com.flowclip.app.STATUS";
    static final String ACTION_UPLOAD_FILE = "com.flowclip.app.UPLOAD_FILE";
    static final String ACTION_UPLOAD_FILES = "com.flowclip.app.UPLOAD_FILES";
    static final String ACTION_REFRESH_FILES = "com.flowclip.app.REFRESH_FILES";
    static final String ACTION_DOWNLOAD_FILE = "com.flowclip.app.DOWNLOAD_FILE";
    static final String ACTION_DELETE_FILE = "com.flowclip.app.DELETE_FILE";
    static final String ACTION_CANCEL_TRANSFER = "com.flowclip.app.CANCEL_TRANSFER";
    static final String EXTRA_LEVEL = "level";
    static final String EXTRA_MESSAGE = "message";
    static final String EXTRA_FILE_ID = "fileId";
    static final String EXTRA_SHARED_TEXT = "sharedText";
    static final String EXTRA_INCOMING_BATCH_TOKEN = "incomingBatchToken";
    static final String EXTRA_FILES_JSON = "filesJson";
    static final String EXTRA_PROGRESS_STAGE = "progressStage";
    static final String EXTRA_PROGRESS_COMPLETED = "progressCompleted";
    static final String EXTRA_PROGRESS_TOTAL = "progressTotal";
    static final String EXTRA_SERVER_URLS = "serverUrls";
    static final String EXTRA_TRANSFER_ACTIVE = "transferActive";

    private static final String CHANNEL_SYNC = "flowclip_sync";
    private static final String CHANNEL_INCOMING = "flowclip_incoming";
    private static final int NOTIFICATION_SYNC = 1001;
    private static final int NOTIFICATION_CONTENT = 1002;
    private static final long PROGRESS_INTERVAL_MILLIS = 200L;
    private static final long BATCH_PROGRESS_PHASE = 1_000_000L;
    private static final long BATCH_PROGRESS_ITEM = BATCH_PROGRESS_PHASE * 2L;
    private static volatile boolean transferActiveState;

    private final Object sessionLock = new Object();
    private final Object transferLock = new Object();
    private final Set<String> incomingBatchTokens = ConcurrentHashMap.newKeySet();
    private Session activeSession;
    private EmbeddedServer embeddedServer;
    private String lastServiceNotificationText;
    private ConnectivityManager connectivityManager;
    private ConnectivityManager.NetworkCallback networkCallback;
    private ExecutorService transferExecutor;
    private Future<?> activeTransfer;
    private FileClient.Cancellation transferCancellation;
    private boolean foregroundStarted;
    private volatile boolean destroyed;
    private Handler mainHandler;
    private int latestStartId;
    private long lastProgressAtMillis;
    private String lastProgressStage;

    @Override
    public void onCreate() {
        super.onCreate();
        mainHandler = new Handler(Looper.getMainLooper());
        createChannels();
        transferExecutor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "flowclip-file-transfer");
            thread.setDaemon(true);
            return thread;
        });
        connectivityManager = getSystemService(ConnectivityManager.class);
        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                Session session;
                synchronized (sessionLock) {
                    session = activeSession;
                    if (session != null) session.failures = 0;
                }
                if (session != null && session.config.autoReceive) schedule(session, 0L);
                if (session != null && session.config.serverEnabled) {
                    publishServerAddressesIfActive(session);
                }
            }

            @Override
            public void onLinkPropertiesChanged(Network network, LinkProperties linkProperties) {
                Session session;
                synchronized (sessionLock) {
                    session = activeSession;
                }
                if (session != null && session.config.serverEnabled) {
                    publishServerAddressesIfActive(session);
                }
            }

            @Override
            public void onLost(Network network) {
                if (connectivityManager.getActiveNetwork() != null) return;
                Session session;
                synchronized (sessionLock) {
                    session = activeSession;
                }
                if (session != null) {
                    if (session.config.serverEnabled) publishServerAddressesIfActive(session);
                    updateServiceNotification(session, "网络已断开，等待恢复");
                }
            }
        };
        connectivityManager.registerDefaultNetworkCallback(networkCallback);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        latestStartId = Math.max(latestStartId, startId);
        String action = intent == null ? ACTION_START : intent.getAction();
        String incomingBatchToken = intent == null
                ? null : intent.getStringExtra(EXTRA_INCOMING_BATCH_TOKEN);
        if (ACTION_STOP.equals(action)) {
            AppConfig.disableBackgroundFeatures(this);
            cancelTransfer();
            stopSync("后台服务已停止");
            stopSelf();
            return START_NOT_STICKY;
        }

        if (ACTION_CANCEL_TRANSFER.equals(action)) {
            try {
                ensureForeground("正在取消文件传输");
            } catch (RuntimeException exception) {
                publishTransferActive(false);
                publishStatus("error", getString(R.string.sync_start_rejected));
                stopSelf();
                return START_NOT_STICKY;
            }
            cancelTransfer();
            publishStatus("warning", "正在取消文件传输");
            if (!hasActiveTransfer()) publishTransferActive(false);
            finishForegroundIfIdle();
            return backgroundRequired() ? START_STICKY : START_NOT_STICKY;
        }

        AppConfig config = AppConfig.load(this);
        if (!config.isConfigured()) {
            releaseIncomingBatch(incomingBatchToken);
            publishTransferActive(false);
            publishStatus("warning", "FlowClip 尚未完成配置");
            stopSelf();
            return START_NOT_STICKY;
        }

        try {
            ensureForeground("正在启动 FlowClip 服务");
        } catch (RuntimeException exception) {
            releaseIncomingBatch(incomingBatchToken);
            publishTransferActive(false);
            publishStatus("error", getString(R.string.sync_start_rejected));
            stopSelf();
            return START_NOT_STICKY;
        }

        if (ACTION_START.equals(action) || action == null) {
            if (!config.serviceRequired()) {
                stopSync("后台服务未启用");
                stopSelf();
                return START_NOT_STICKY;
            }
            try {
                Session current;
                synchronized (sessionLock) {
                    current = activeSession;
                }
                if (current != null && sameServiceConfiguration(current.config, config)) {
                    if (config.serverEnabled) publishServerAddresses(config);
                    updateServiceNotification(current, roleText(config) + "正在运行");
                    publishStatus("success", roleText(config) + "正在运行");
                    return START_STICKY;
                }
                replaceSession(config);
                publishStatus("success", roleText(config) + "已启动");
                return START_STICKY;
            } catch (IOException | RuntimeException exception) {
                publishStatus("error", messageOf(exception));
                stopSync("后台服务启动失败");
                stopSelf();
                return START_NOT_STICKY;
            }
        }

        try {
            reconcileBackgroundSession(config);
        } catch (IOException | RuntimeException exception) {
            releaseIncomingBatch(incomingBatchToken);
            publishTransferActive(false);
            publishStatus("error", messageOf(exception));
            finishForegroundIfIdle();
            return START_NOT_STICKY;
        }
        if (ACTION_UPLOAD_FILE.equals(action) || ACTION_UPLOAD_FILES.equals(action)) {
            final List<Uri> uploadUris;
            try {
                uploadUris = uploadUris(
                        intent,
                        incomingBatchToken,
                        ACTION_UPLOAD_FILES.equals(action));
            } catch (IOException exception) {
                releaseIncomingBatch(incomingBatchToken);
                publishStatus("error", messageOf(exception));
                publishTransferActive(false);
                finishForegroundIfIdle();
                return backgroundRequired() ? START_STICKY : START_NOT_STICKY;
            }
            String sharedText = ACTION_UPLOAD_FILES.equals(action) && intent != null
                    ? intent.getStringExtra(EXTRA_SHARED_TEXT) : null;
            if (uploadUris.isEmpty() && (sharedText == null || sharedText.isEmpty())) {
                releaseIncomingBatch(incomingBatchToken);
                publishStatus("error", "所选内容无法读取");
                publishTransferActive(false);
                finishForegroundIfIdle();
            } else {
                if (incomingBatchToken != null) incomingBatchTokens.add(incomingBatchToken);
                boolean started = startTransfer(config, (client, progress) -> {
                    try {
                        uploadIncomingBatch(config, client, progress, uploadUris, sharedText);
                    } finally {
                        releaseIncomingBatch(incomingBatchToken);
                    }
                });
                if (!started) releaseIncomingBatch(incomingBatchToken);
            }
        } else if (ACTION_REFRESH_FILES.equals(action)) {
            startTransfer(config, (client, progress) -> {
                FileClient.Cancellation cancellation = currentCancellation();
                publishRemoteFiles(client, cancellation);
                cancellation.throwIfCancelled();
                if (destroyed) return;
                publishStatus("success", "文件列表已刷新");
            });
        } else if (ACTION_DOWNLOAD_FILE.equals(action)) {
            String id = intent == null ? null : intent.getStringExtra(EXTRA_FILE_ID);
            startTransfer(config, (client, progress) -> downloadFile(client, id, progress));
        } else if (ACTION_DELETE_FILE.equals(action)) {
            String id = intent == null ? null : intent.getStringExtra(EXTRA_FILE_ID);
            startTransfer(config, (client, progress) -> {
                if (id == null || id.isEmpty()) throw new IOException("文件 ID 无效");
                FileClient.Cancellation cancellation = currentCancellation();
                client.delete(id, cancellation);
                publishRemoteFiles(client, cancellation);
                cancellation.throwIfCancelled();
                if (destroyed) return;
                publishStatus("success", "文件已从服务器删除");
            });
        } else {
            releaseIncomingBatch(incomingBatchToken);
            publishStatus("error", "不支持的后台操作");
            publishTransferActive(false);
            finishForegroundIfIdle();
        }
        return backgroundRequired() ? START_STICKY : START_NOT_STICKY;
    }

    private void replaceSession(AppConfig config) throws IOException {
        Session replacement = new Session(config);
        Session previous;
        EmbeddedServer previousServer;
        synchronized (sessionLock) {
            previous = activeSession;
            previousServer = embeddedServer;
            activeSession = null;
            embeddedServer = null;
        }
        if (previous != null) previous.executor.shutdownNow();
        if (previousServer != null) previousServer.stopServer();

        EmbeddedServer replacementServer = null;
        if (config.serverEnabled) {
            replacementServer = new EmbeddedServer(
                    this,
                    config.listenPort,
                    config.token,
                    config.maxBytes(),
                    config.fileMaxBytes(),
                    new EmbeddedServer.Listener() {
                        @Override
                        public void onClipboardReceived(ClipItem item) {
                            synchronized (sessionLock) {
                                if (activeSession != replacement) return;
                                publishStatus("success", "手机服务器收到"
                                        + ("image".equals(item.kind) ? "图片" : "文本"));
                            }
                        }

                        @Override
                        public void onFilesChanged(
                                FileStore.Snapshot snapshot, FileStore.StoredFile received) {
                            synchronized (sessionLock) {
                                if (activeSession != replacement || embeddedServer == null) return;
                                publishFiles(snapshotJson(embeddedServer.fileSnapshot()));
                                if (received != null) {
                                    showFileNotification(
                                            received.metadata, received.uri, received.displayPath);
                                    publishStatus("success", "文件已保存到 " + received.displayPath);
                                }
                            }
                        }
                    });
        }
        try {
            synchronized (sessionLock) {
                if (replacementServer != null) replacementServer.startServer();
                activeSession = replacement;
                embeddedServer = replacementServer;
                lastServiceNotificationText = null;
                if (replacementServer != null) {
                    publishFiles(snapshotJson(replacementServer.fileSnapshot()));
                }
                publishServerAddresses(config);
            }
        } catch (IOException | RuntimeException exception) {
            synchronized (sessionLock) {
                if (activeSession == replacement) {
                    activeSession = null;
                    embeddedServer = null;
                    lastServiceNotificationText = null;
                }
            }
            replacement.executor.shutdownNow();
            if (replacementServer != null) replacementServer.stopServer();
            throw exception;
        }
        if (config.autoReceive) schedule(replacement, 0L);
        updateServiceNotification(replacement, roleText(config) + "正在运行");
    }

    private void reconcileBackgroundSession(AppConfig config) throws IOException {
        Session current;
        synchronized (sessionLock) {
            current = activeSession;
        }
        if (config.serviceRequired()) {
            if (current == null || !sameServiceConfiguration(current.config, config)) {
                replaceSession(config);
            }
            return;
        }
        if (current != null || embeddedServer != null) clearBackgroundSession();
        publishServerAddresses(config);
    }

    private void schedule(Session session, long delayMillis) {
        if (!session.config.autoReceive) return;
        synchronized (sessionLock) {
            if (activeSession != session) return;
            if (session.pending != null && !session.pending.isDone()) {
                if (session.pending.getDelay(TimeUnit.MILLISECONDS) <= delayMillis) return;
                session.pending.cancel(false);
            }
            try {
                session.pending = session.executor.schedule(() -> {
                    synchronized (sessionLock) {
                        if (activeSession != session) return;
                        session.pending = null;
                    }
                    poll(session);
                }, delayMillis, TimeUnit.MILLISECONDS);
            } catch (RejectedExecutionException ignored) {
                // The session was replaced or stopped while this poll was finishing.
            }
        }
    }

    private void poll(Session session) {
        if (!isActive(session)) return;
        long nextDelay;
        try {
            receiveOnce(session);
            synchronized (sessionLock) {
                if (activeSession != session) return;
                session.failures = 0;
            }
            nextDelay = session.baseDelayMillis;
            updateServiceNotification(session, roleText(session.config) + "：已连接");
        } catch (IOException | JSONException | RuntimeException exception) {
            synchronized (sessionLock) {
                if (activeSession != session) return;
                session.failures = Math.min(
                        session.failures + 1, BackoffPolicy.MAX_FAILURES);
                nextDelay = BackoffPolicy.delayMillis(
                        session.baseDelayMillis, session.failures);
            }
            String detail = exception.getMessage();
            if (detail == null || detail.trim().isEmpty()) {
                detail = exception.getClass().getSimpleName();
            }
            publishStatusIfActive(session, "error", detail);
            updateServiceNotification(session,
                    "连接异常，约 " + Math.max(1L, nextDelay / 1000L) + " 秒后重试");
        }
        schedule(session, nextDelay);
    }

    private void receiveOnce(Session session) throws IOException, JSONException {
        synchronized (SyncCursor.TRANSACTION_LOCK) {
            if (!isActive(session)) return;
            long knownRevision = SyncCursor.load(this, session.config);
            ApiClient.FetchResult result = session.client.fetch(knownRevision);
            ClipItem item = result.item;
            if (item != null
                    && !session.config.deviceId.equals(item.origin)
                    && !"text".equals(item.kind)) {
                ImageStorage.PendingImage pending = ImageStorage.prepare(
                        this, item, () -> isActive(session));
                try {
                    synchronized (sessionLock) {
                        if (activeSession != session) return;
                        ImageStorage.SavedImage image = pending.commit();
                        try {
                            SyncCursor.save(this, session.config, result.revision);
                        } catch (IOException exception) {
                            showImageNotification(item, image);
                            throw new IOException(
                                    "图片已保存，但同步进度写入失败，后续可能重复接收",
                                    exception);
                        }
                        showImageNotification(item, image);
                        publishStatus("success", "图片已保存到 " + image.displayPath);
                    }
                } finally {
                    pending.abort();
                }
                return;
            }
            synchronized (sessionLock) {
                if (activeSession != session) return;
                if (item != null && !session.config.deviceId.equals(item.origin)) {
                    ClipboardBridge.applyText(this, item);
                    SyncCursor.save(this, session.config, result.revision);
                    showTextNotification();
                    publishStatus("success", "已收到文本并复制到剪贴板");
                } else {
                    SyncCursor.save(this, session.config, result.revision);
                }
            }
        }
    }

    private void showTextNotification() {
        PendingIntent pending = PendingIntent.getActivity(
                this, 0, launchIntent(), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, CHANNEL_INCOMING)
                .setSmallIcon(R.drawable.ic_sync)
                .setContentTitle(getString(R.string.text_received))
                .setContentText(getString(R.string.content_hidden))
                .setContentIntent(pending)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setAutoCancel(true)
                .build();
        getSystemService(NotificationManager.class).notify(NOTIFICATION_CONTENT, notification);
    }

    private void showImageNotification(ClipItem item, ImageStorage.SavedImage image) {
        Intent view = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(image.uri, item.mime)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        PendingIntent pending = PendingIntent.getActivity(
                this, 1, view, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        int message = image.publicGallery
                ? R.string.open_image_public : R.string.open_image_private;
        Notification notification = new Notification.Builder(this, CHANNEL_INCOMING)
                .setSmallIcon(R.drawable.ic_sync)
                .setContentTitle(getString(R.string.image_received))
                .setContentText(getString(message))
                .setContentIntent(pending)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setAutoCancel(true)
                .build();
        getSystemService(NotificationManager.class).notify(NOTIFICATION_CONTENT, notification);
    }

    private void showFileNotification(FileMetadata metadata, Uri uri, String displayPath) {
        Intent view = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, metadata.mime)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        int requestCode = 100 + Math.abs(metadata.id.hashCode() % 10_000);
        PendingIntent pending = PendingIntent.getActivity(
                this,
                requestCode,
                view,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, CHANNEL_INCOMING)
                .setSmallIcon(R.drawable.ic_sync)
                .setContentTitle("已收到文件：" + metadata.filename)
                .setContentText(displayPath)
                .setContentIntent(pending)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setAutoCancel(true)
                .build();
        getSystemService(NotificationManager.class)
                .notify(2_000 + Math.abs(metadata.id.hashCode() % 10_000), notification);
    }

    private Intent launchIntent() {
        return new Intent(this, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
    }

    private Notification buildServiceNotification(String text) {
        PendingIntent pending = PendingIntent.getActivity(
                this, 2, launchIntent(), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(
                this,
                3,
                new Intent(this, SyncService.class).setAction(ACTION_STOP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = new Notification.Builder(this, CHANNEL_SYNC)
                .setSmallIcon(R.drawable.ic_sync)
                .setContentTitle(getString(R.string.service_running))
                .setContentText(text)
                .setContentIntent(pending)
                .addAction(R.drawable.ic_sync, getString(R.string.stop_sync), stop)
                .setOngoing(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .setVisibility(Notification.VISIBILITY_PRIVATE);
        synchronized (transferLock) {
            if (activeTransfer != null && !activeTransfer.isDone()) {
                PendingIntent cancel = PendingIntent.getService(
                        this,
                        4,
                        new Intent(this, SyncService.class).setAction(ACTION_CANCEL_TRANSFER),
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
                builder.addAction(R.drawable.ic_sync, "取消传输", cancel);
            }
        }
        return builder.build();
    }

    private void ensureForeground(String text) {
        if (!foregroundStarted) {
            startForeground(NOTIFICATION_SYNC, buildServiceNotification(text));
            foregroundStarted = true;
        } else {
            notifyService(text);
        }
    }

    private void notifyService(String text) {
        if (destroyed) return;
        getSystemService(NotificationManager.class)
                .notify(NOTIFICATION_SYNC, buildServiceNotification(text));
    }

    private void updateServiceNotification(Session session, String text) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            Handler handler = mainHandler;
            if (handler != null) handler.post(() -> updateServiceNotification(session, text));
            return;
        }
        if (destroyed) return;
        synchronized (sessionLock) {
            if (activeSession != session || text.equals(lastServiceNotificationText)) return;
            lastServiceNotificationText = text;
        }
        getSystemService(NotificationManager.class)
                .notify(NOTIFICATION_SYNC, buildServiceNotification(text));
    }

    private void createChannels() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel sync = new NotificationChannel(
                CHANNEL_SYNC, getString(R.string.notification_channel_sync),
                NotificationManager.IMPORTANCE_LOW);
        sync.setDescription(getString(R.string.service_description));
        manager.createNotificationChannel(sync);
        NotificationChannel incoming = new NotificationChannel(
                CHANNEL_INCOMING, getString(R.string.notification_channel_incoming),
                NotificationManager.IMPORTANCE_DEFAULT);
        incoming.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        manager.createNotificationChannel(incoming);
    }

    private void publishStatus(String level, String message) {
        getSharedPreferences("flowclip_runtime", MODE_PRIVATE).edit()
                .putString("level", level)
                .putString("message", message)
                .apply();
        Intent status = new Intent(ACTION_STATUS)
                .setPackage(getPackageName())
                .putExtra(EXTRA_LEVEL, level)
                .putExtra(EXTRA_MESSAGE, message);
        sendStatusBroadcast(status);
    }

    private void publishStatusIfActive(Session session, String level, String message) {
        synchronized (sessionLock) {
            if (activeSession != session) return;
            publishStatus(level, message);
        }
    }

    private void publishFiles(String json) {
        getSharedPreferences("flowclip_runtime", MODE_PRIVATE).edit()
                .putString("filesJson", json)
                .apply();
        sendStatusBroadcast(new Intent(ACTION_STATUS)
                .setPackage(getPackageName())
                .putExtra(EXTRA_FILES_JSON, json));
    }

    private void publishProgress(
            FileClient.Cancellation expected, String stage, long completed, long total) {
        long safeTotal = Math.max(total, 1L);
        int percent = (int) Math.min(100L, completed * 100L / safeTotal);
        long now = SystemClock.elapsedRealtime();
        synchronized (transferLock) {
            if (destroyed || transferCancellation != expected) return;
            boolean stageChanged = !Objects.equals(lastProgressStage, stage);
            if (!stageChanged
                    && now - lastProgressAtMillis < PROGRESS_INTERVAL_MILLIS) return;
            lastProgressAtMillis = now;
            lastProgressStage = stage;
        }
        Handler handler = mainHandler;
        if (handler == null) return;
        handler.post(() -> {
            synchronized (transferLock) {
                if (destroyed || transferCancellation != expected) return;
                notifyService(stage + " " + percent + "%");
                sendStatusBroadcast(new Intent(ACTION_STATUS)
                        .setPackage(getPackageName())
                        .putExtra(EXTRA_PROGRESS_STAGE, stage)
                        .putExtra(EXTRA_PROGRESS_COMPLETED, completed)
                        .putExtra(EXTRA_PROGRESS_TOTAL, total));
            }
        });
    }

    private void publishServerAddresses(AppConfig config) {
        String rendered = config.serverEnabled
                ? String.join("\n", LocalAddressResolver.httpUrls(config.listenPort))
                : "";
        getSharedPreferences("flowclip_runtime", MODE_PRIVATE).edit()
                .putString("serverUrls", rendered)
                .apply();
        sendStatusBroadcast(new Intent(ACTION_STATUS)
                .setPackage(getPackageName())
                .putExtra(EXTRA_SERVER_URLS, rendered));
    }

    private void publishServerAddressesIfActive(Session session) {
        synchronized (sessionLock) {
            if (activeSession != session) return;
            publishServerAddresses(session.config);
        }
    }

    private boolean startTransfer(AppConfig config, TransferOperation operation) {
        synchronized (transferLock) {
            if (activeTransfer != null && !activeTransfer.isDone()) {
                publishStatus("warning", "已有文件传输正在进行");
                return false;
            }
            FileClient.Cancellation cancellation = new FileClient.Cancellation();
            transferCancellation = cancellation;
            lastProgressAtMillis = 0L;
            lastProgressStage = null;
            try {
                activeTransfer = transferExecutor.submit(() -> {
                    try {
                        operation.run(
                                new FileClient(this, config),
                                (stage, completed, total) -> publishProgress(
                                        cancellation, stage, completed, total));
                    } catch (IOException | JSONException | RuntimeException exception) {
                        if (!destroyed) publishStatus("error", messageOf(exception));
                    } finally {
                        synchronized (transferLock) {
                            activeTransfer = null;
                            transferCancellation = null;
                            if (!destroyed) publishTransferActive(false);
                        }
                        if (!destroyed) finishForegroundIfIdle();
                    }
                });
                publishTransferActive(true);
            } catch (RejectedExecutionException exception) {
                transferCancellation = null;
                activeTransfer = null;
                publishTransferActive(false);
                publishStatus("error", "文件传输服务已停止");
                finishForegroundIfIdle();
                return false;
            }
        }
        notifyService("文件传输已开始");
        return true;
    }

    private void publishTransferActive(boolean active) {
        transferActiveState = active;
        sendStatusBroadcast(new Intent(ACTION_STATUS)
                .setPackage(getPackageName())
                .putExtra(EXTRA_TRANSFER_ACTIVE, active));
    }

    static boolean isTransferActiveForUi() {
        return transferActiveState;
    }

    private void sendStatusBroadcast(Intent intent) {
        sendBroadcast(intent, getPackageName() + ".permission.INTERNAL_STATUS");
    }

    private List<Uri> uploadUris(
            Intent intent, String incomingBatchToken, boolean requireStagedUris)
            throws IOException {
        List<Uri> result = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        ClipData clipData = intent == null ? null : intent.getClipData();
        if (clipData != null) {
            try {
                IncomingContentPolicy.requireItemCount(clipData.getItemCount());
            } catch (IllegalArgumentException exception) {
                throw new IOException(exception.getMessage(), exception);
            }
            for (int index = 0; index < clipData.getItemCount(); index++) {
                addUploadUri(
                        result,
                        seen,
                        clipData.getItemAt(index).getUri(),
                        incomingBatchToken,
                        requireStagedUris);
            }
        }
        if (intent != null) {
            addUploadUri(
                    result,
                    seen,
                    intent.getData(),
                    incomingBatchToken,
                    requireStagedUris);
        }
        return result;
    }

    private void addUploadUri(
            List<Uri> result,
            Set<String> seen,
            Uri uri,
            String incomingBatchToken,
            boolean requireStagedUri)
            throws IOException {
        if (uri == null) return;
        if (!"content".equalsIgnoreCase(uri.getScheme())) {
            throw new IOException("仅支持系统提供的 content:// 文件");
        }
        if (requireStagedUri && !isExpectedIncomingUri(uri, incomingBatchToken)) {
            throw new IOException("文件批次 URI 与临时授权不匹配");
        }
        String key = uri.normalizeScheme().toString();
        if (!seen.add(key)) return;
        try {
            IncomingContentPolicy.requireItemCount(result.size() + 1);
        } catch (IllegalArgumentException exception) {
            throw new IOException(exception.getMessage(), exception);
        }
        result.add(uri);
    }

    private boolean isExpectedIncomingUri(Uri uri, String token) {
        if (token == null || token.isEmpty()
                || !(getPackageName() + ".files").equals(uri.getAuthority())) {
            return false;
        }
        List<String> segments = uri.getPathSegments();
        if (segments.size() != 3
                || !"incoming".equals(segments.get(0))
                || !token.equals(segments.get(1))) {
            return false;
        }
        try {
            int index = Integer.parseInt(segments.get(2));
            return index >= 0 && index < IncomingContentPolicy.MAX_ITEMS;
        } catch (NumberFormatException exception) {
            return false;
        }
    }

    private void uploadIncomingBatch(
            AppConfig config,
            FileClient client,
            FileClient.ProgressListener progress,
            List<Uri> uris,
            String sharedText) throws IOException, JSONException {
        boolean hasText = sharedText != null && !sharedText.isEmpty();
        int totalItems = uris.size() + (hasText ? 1 : 0);
        try {
            IncomingContentPolicy.requireItemCount(totalItems);
        } catch (IllegalArgumentException exception) {
            throw new IOException(exception.getMessage(), exception);
        }
        FileClient.Cancellation cancellation = currentCancellation();
        int completedItems = 0;

        int uploadedFiles = 0;
        FileMetadata lastStored = null;
        try {
            if (hasText) {
                reportBatchMarker(
                        progress, "正在发送文字 (1/" + totalItems + ")", 0, totalItems);
                ClipItem text = ClipItem.text(config.deviceId, sharedText);
                if (text.data.length > config.maxBytes()) {
                    throw new IOException("文本超过大小限制");
                }
                new ApiClient(config).push(text);
                completedItems++;
                reportBatchMarker(
                        progress,
                        "文字已发送 (1/" + totalItems + ")",
                        completedItems,
                        totalItems);
                cancellation.throwIfCancelled();
            }

            for (int index = 0; index < uris.size(); index++) {
                cancellation.throwIfCancelled();
                int itemNumber = completedItems + 1;
                FileClient.ProgressListener itemProgress = batchItemProgress(
                        progress, itemNumber, totalItems);
                lastStored = client.upload(uris.get(index), itemProgress, cancellation);
                uploadedFiles++;
                completedItems++;
                reportBatchMarker(
                        progress,
                        "已发送 " + completedItems + "/" + totalItems,
                        completedItems,
                        totalItems);
                cancellation.throwIfCancelled();
            }
        } catch (IOException | JSONException exception) {
            if (completedItems == 0 && !cancellation.isCancelled()) throw exception;
            boolean listRefreshed = uploadedFiles == 0;
            if (uploadedFiles > 0 && !destroyed) {
                try {
                    publishRemoteFiles(client, new FileClient.Cancellation());
                    listRefreshed = true;
                } catch (IOException | JSONException | RuntimeException ignored) {
                    // The status below still reports exactly what was already committed.
                }
            }
            String reason = cancellation.isCancelled()
                    ? "批次已取消" : "后续内容发送失败：" + messageOf(exception);
            String refresh = listRefreshed ? "" : "；请刷新文件列表";
            publishStatus(
                    "warning",
                    "已发送 " + completedItems + "/" + totalItems + " 项；" + reason + refresh);
            return;
        }

        if (!uris.isEmpty()) publishRemoteFiles(client, cancellation);
        cancellation.throwIfCancelled();
        if (destroyed) return;
        if (hasText && !uris.isEmpty()) {
            publishStatus("success", "文字和 " + uris.size() + " 个文件已发送");
        } else if (lastStored != null && uris.size() == 1) {
            publishStatus("success", "文件已发送：" + lastStored.filename);
        } else if (!uris.isEmpty()) {
            publishStatus("success", uris.size() + " 个文件已发送");
        } else {
            publishStatus("success", "文字已发送");
        }
    }

    private static FileClient.ProgressListener batchItemProgress(
            FileClient.ProgressListener target, int itemNumber, int totalItems) {
        long base = (long) (itemNumber - 1) * BATCH_PROGRESS_ITEM;
        long overallTotal = (long) totalItems * BATCH_PROGRESS_ITEM;
        return (stage, completed, total) -> {
            long boundedTotal = Math.max(0L, total);
            long boundedCompleted = Math.max(0L, completed);
            long ratio = 0L;
            if (boundedTotal > 0L) {
                ratio = Math.min(boundedCompleted, boundedTotal)
                        * BATCH_PROGRESS_PHASE / boundedTotal;
            }
            long phase = "正在上传".equals(stage) ? BATCH_PROGRESS_PHASE : 0L;
            String label = stage + " (" + itemNumber + "/" + totalItems + ")";
            target.onProgress(label, base + phase + ratio, overallTotal);
        };
    }

    private static void reportBatchMarker(
            FileClient.ProgressListener target, String stage, int completedItems, int totalItems) {
        target.onProgress(
                stage,
                (long) completedItems * BATCH_PROGRESS_ITEM,
                (long) totalItems * BATCH_PROGRESS_ITEM);
    }

    private void releaseIncomingBatch(String token) {
        if (token == null || token.isEmpty()) return;
        incomingBatchTokens.remove(token);
        IncomingFileRegistry.discard(token);
    }

    private void downloadFile(
            FileClient client, String id, FileClient.ProgressListener progress)
            throws IOException, JSONException {
        if (id == null || id.isEmpty()) throw new IOException("文件 ID 无效");
        FileClient.Cancellation cancellation = currentCancellation();
        FileClient.ListResult list = client.list(cancellation);
        FileMetadata selected = null;
        for (FileMetadata item : list.files) {
            if (id.equals(item.id)) {
                selected = item;
                break;
            }
        }
        if (selected == null) throw new IOException("文件不存在");
        FileClient.DownloadedFile downloaded = client.download(
                selected, progress, cancellation);
        cancellation.throwIfCancelled();
        if (destroyed) return;
        showFileNotification(downloaded.metadata, downloaded.uri, downloaded.displayPath);
        publishStatus("success", "文件已保存到 " + downloaded.displayPath);
    }

    private void publishRemoteFiles(FileClient client, FileClient.Cancellation cancellation)
            throws IOException, JSONException {
        String json = client.list(cancellation).toJson().toString();
        cancellation.throwIfCancelled();
        if (!destroyed) publishFiles(json);
    }

    private FileClient.Cancellation currentCancellation() throws IOException {
        synchronized (transferLock) {
            if (transferCancellation == null) throw new IOException("文件传输未启动");
            return transferCancellation;
        }
    }

    private void cancelTransfer() {
        synchronized (transferLock) {
            if (transferCancellation != null) transferCancellation.cancel();
        }
    }

    private boolean hasActiveTransfer() {
        synchronized (transferLock) {
            return activeTransfer != null && !activeTransfer.isDone();
        }
    }

    private boolean backgroundRequired() {
        synchronized (sessionLock) {
            return activeSession != null && activeSession.config.serviceRequired();
        }
    }

    private void finishForegroundIfIdle() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            Handler handler = mainHandler;
            if (handler != null) handler.post(this::finishForegroundIfIdle);
            return;
        }
        if (destroyed) return;
        if (hasActiveTransfer()) return;
        Session session;
        synchronized (sessionLock) {
            session = activeSession;
        }
        if (session != null && session.config.serviceRequired()) {
            notifyService(roleText(session.config) + "正在运行");
            return;
        }
        if (!stopSelfResult(latestStartId)) return;
        if (foregroundStarted) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            foregroundStarted = false;
        }
    }

    private static String roleText(AppConfig config) {
        if (config.serverEnabled && config.autoReceive) return "手机服务器和自动接收";
        if (config.serverEnabled) return "手机服务器";
        return "后台自动接收";
    }

    private static boolean sameServiceConfiguration(AppConfig first, AppConfig second) {
        return Objects.equals(first.deviceId, second.deviceId)
                && Objects.equals(first.deviceName, second.deviceName)
                && Objects.equals(first.serverUrl, second.serverUrl)
                && Objects.equals(first.token, second.token)
                && first.allowInsecureLan == second.allowInsecureLan
                && first.requireHttpsForPublic == second.requireHttpsForPublic
                && first.autoReceive == second.autoReceive
                && Float.compare(first.pollIntervalSeconds, second.pollIntervalSeconds) == 0
                && first.maxSizeMb == second.maxSizeMb
                && first.serverEnabled == second.serverEnabled
                && first.listenPort == second.listenPort
                && first.fileMaxMb == second.fileMaxMb;
    }

    private static String messageOf(Throwable exception) {
        String message = exception.getMessage();
        return message == null || message.trim().isEmpty()
                ? exception.getClass().getSimpleName() : message;
    }

    private static String snapshotJson(FileStore.Snapshot snapshot) {
        try {
            return snapshot.toJson().toString();
        } catch (JSONException impossible) {
            throw new AssertionError(impossible);
        }
    }

    private boolean isActive(Session session) {
        synchronized (sessionLock) {
            return activeSession == session;
        }
    }

    private void stopSync(String message) {
        clearBackgroundSession();
        if (foregroundStarted) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            foregroundStarted = false;
        }
        publishStatus("warning", message);
    }

    private void clearBackgroundSession() {
        Session previous;
        EmbeddedServer previousServer;
        synchronized (sessionLock) {
            previous = activeSession;
            previousServer = embeddedServer;
            activeSession = null;
            embeddedServer = null;
            lastServiceNotificationText = null;
        }
        if (previous != null) previous.executor.shutdownNow();
        if (previousServer != null) previousServer.stopServer();
    }

    @Override
    public void onDestroy() {
        destroyed = true;
        if (mainHandler != null) mainHandler.removeCallbacksAndMessages(null);
        clearBackgroundSession();
        cancelTransfer();
        if (transferExecutor != null) transferExecutor.shutdownNow();
        for (String token : new ArrayList<>(incomingBatchTokens)) {
            releaseIncomingBatch(token);
        }
        publishTransferActive(false);
        if (connectivityManager != null && networkCallback != null) {
            try {
                connectivityManager.unregisterNetworkCallback(networkCallback);
            } catch (IllegalArgumentException ignored) {
                // The callback may already have been removed during process shutdown.
            }
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private interface TransferOperation {
        void run(FileClient client, FileClient.ProgressListener progress)
                throws IOException, JSONException;
    }

    private static final class Session {
        final AppConfig config;
        final ApiClient client;
        final ScheduledExecutorService executor;
        final long baseDelayMillis;
        ScheduledFuture<?> pending;
        int failures;

        Session(AppConfig config) {
            this.config = config;
            this.client = new ApiClient(config);
            this.baseDelayMillis = Math.max(500L, (long) (config.pollIntervalSeconds * 1000L));
            this.executor = Executors.newSingleThreadScheduledExecutor(runnable -> {
                Thread thread = new Thread(runnable, "flowclip-android-receive");
                thread.setDaemon(true);
                return thread;
            });
        }
    }
}
