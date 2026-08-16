package com.flowclip.app;

import android.annotation.SuppressLint;
import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.Html;
import android.text.InputType;
import android.text.TextUtils;
import android.view.DragAndDropPermissions;
import android.view.DragEvent;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;

public final class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 2001;
    private static final int REQUEST_FILE = 2002;
    private static final String STATE_PENDING_SHARE = "pendingShare";
    private static final String STATE_SHARE_CONSUMED = "shareConsumed";
    private static final String EXTRA_CLIPBOARD_ACTION_ACTIVE = "clipboardActionActive";
    private static final ExecutorService ACTIONS = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "flowclip-android-action");
        thread.setDaemon(true);
        return thread;
    });
    private static final AtomicBoolean CLIPBOARD_ACTION_ACTIVE = new AtomicBoolean();

    private EditText deviceName;
    private EditText serverUrl;
    private EditText token;
    private EditText pollInterval;
    private EditText maxSize;
    private EditText listenPort;
    private EditText fileMaxSize;
    private CheckBox showToken;
    private CheckBox allowInsecureLan;
    private CheckBox requireHttpsForPublic;
    private Switch autoReceive;
    private Switch serverEnabled;
    private TextView publicHttpRisk;
    private TextView localServerUrls;
    private TextView statusText;
    private TextView transferProgressText;
    private TextView filesEmpty;
    private TextView dropHint;
    private ProgressBar transferProgress;
    private LinearLayout transferStatus;
    private LinearLayout fileList;
    private Button saveStart;
    private Button sendClipboard;
    private Button receiveNow;
    private Button testConnection;
    private Button stopSync;
    private Button backgroundSettings;
    private Button chooseFile;
    private Button refreshFiles;
    private Button cancelTransfer;

    private AppConfig config;
    private Intent pendingShareIntent;
    private DragAndDropPermissions activeDropPermissions;
    private boolean receiverRegistered;
    private boolean actionRunning;
    private boolean transferRunning;
    private boolean shareConsumed;

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String level = intent.getStringExtra(SyncService.EXTRA_LEVEL);
            String message = intent.getStringExtra(SyncService.EXTRA_MESSAGE);
            if (message != null) setStatus(level, message);
            if (intent.hasExtra(SyncService.EXTRA_SERVER_URLS)) {
                showServerUrls(intent.getStringExtra(SyncService.EXTRA_SERVER_URLS));
            }
            if (intent.hasExtra(SyncService.EXTRA_FILES_JSON)) {
                renderFilesJson(intent.getStringExtra(SyncService.EXTRA_FILES_JSON));
            }
            if (intent.hasExtra(SyncService.EXTRA_PROGRESS_STAGE)) {
                if (SyncService.isTransferActiveForUi()) {
                    showTransferProgress(
                            intent.getStringExtra(SyncService.EXTRA_PROGRESS_STAGE),
                            intent.getLongExtra(SyncService.EXTRA_PROGRESS_COMPLETED, 0L),
                            intent.getLongExtra(SyncService.EXTRA_PROGRESS_TOTAL, 0L));
                }
            }
            if (intent.hasExtra(SyncService.EXTRA_TRANSFER_ACTIVE)) {
                setTransferBusy(SyncService.isTransferActiveForUi());
            }
            if (intent.hasExtra(EXTRA_CLIPBOARD_ACTION_ACTIVE)) {
                setActionBusy(CLIPBOARD_ACTION_ACTIVE.get());
            }
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.activity_main);
        applySystemBarInsets();
        bindViews();
        config = AppConfig.load(this);
        loadFields(config);
        wireActions();
        restoreRuntimeStatus();
        if (state != null) {
            shareConsumed = state.getBoolean(STATE_SHARE_CONSUMED, false);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pendingShareIntent = state.getParcelable(STATE_PENDING_SHARE, Intent.class);
            } else {
                @SuppressWarnings("deprecation")
                Intent legacy = state.getParcelable(STATE_PENDING_SHARE);
                pendingShareIntent = legacy;
            }
        }
        if (!shareConsumed) consumeShareIntent(getIntent());
    }

    private void applySystemBarInsets() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.VANILLA_ICE_CREAM) return;
        View root = findViewById(R.id.rootView);
        int left = root.getPaddingLeft();
        int top = root.getPaddingTop();
        int right = root.getPaddingRight();
        int bottom = root.getPaddingBottom();
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(
                    android.view.WindowInsets.Type.systemBars());
            view.setPadding(left + bars.left, top + bars.top,
                    right + bars.right, bottom + bars.bottom);
            return insets;
        });
        root.requestApplyInsets();
    }

    private void bindViews() {
        deviceName = findViewById(R.id.deviceName);
        serverUrl = findViewById(R.id.serverUrl);
        token = findViewById(R.id.token);
        pollInterval = findViewById(R.id.pollInterval);
        maxSize = findViewById(R.id.maxSize);
        listenPort = findViewById(R.id.listenPort);
        fileMaxSize = findViewById(R.id.fileMaxSize);
        showToken = findViewById(R.id.showToken);
        allowInsecureLan = findViewById(R.id.allowInsecureLan);
        requireHttpsForPublic = findViewById(R.id.requireHttpsForPublic);
        autoReceive = findViewById(R.id.autoReceive);
        serverEnabled = findViewById(R.id.serverEnabled);
        publicHttpRisk = findViewById(R.id.publicHttpRisk);
        localServerUrls = findViewById(R.id.localServerUrls);
        statusText = findViewById(R.id.statusText);
        transferProgressText = findViewById(R.id.transferProgressText);
        filesEmpty = findViewById(R.id.filesEmpty);
        dropHint = findViewById(R.id.dropHint);
        transferProgress = findViewById(R.id.transferProgress);
        transferStatus = findViewById(R.id.transferStatus);
        fileList = findViewById(R.id.fileList);
        saveStart = findViewById(R.id.saveStart);
        sendClipboard = findViewById(R.id.sendClipboard);
        receiveNow = findViewById(R.id.receiveNow);
        testConnection = findViewById(R.id.testConnection);
        stopSync = findViewById(R.id.stopSync);
        backgroundSettings = findViewById(R.id.backgroundSettings);
        chooseFile = findViewById(R.id.chooseFile);
        refreshFiles = findViewById(R.id.refreshFiles);
        cancelTransfer = findViewById(R.id.cancelTransfer);
    }

    private void loadFields(AppConfig value) {
        deviceName.setText(value.deviceName);
        serverUrl.setText(value.serverUrl);
        token.setText(value.token);
        pollInterval.setText(String.valueOf(value.pollIntervalSeconds));
        maxSize.setText(String.valueOf(value.maxSizeMb));
        listenPort.setText(String.valueOf(value.listenPort));
        fileMaxSize.setText(String.valueOf(value.fileMaxMb));
        allowInsecureLan.setChecked(value.allowInsecureLan);
        requireHttpsForPublic.setChecked(value.requireHttpsForPublic);
        updatePublicHttpRisk(value.requireHttpsForPublic);
        autoReceive.setChecked(value.autoReceive);
        serverEnabled.setChecked(value.serverEnabled);
        updateServerMode(value.serverEnabled);
    }

    private void wireActions() {
        showToken.setOnCheckedChangeListener((button, checked) -> {
            int selection = token.getSelectionStart();
            token.setInputType(InputType.TYPE_CLASS_TEXT
                    | (checked ? InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                    : InputType.TYPE_TEXT_VARIATION_PASSWORD));
            token.setSelection(Math.max(0, Math.min(selection, token.length())));
        });
        requireHttpsForPublic.setOnCheckedChangeListener(
                (button, checked) -> updatePublicHttpRisk(checked));
        serverEnabled.setOnCheckedChangeListener(
                (button, checked) -> updateServerMode(checked));
        saveStart.setOnClickListener(view -> saveAndStart());
        sendClipboard.setOnClickListener(view -> runAction("发送完成", current -> {
            ClipItem item = ClipboardBridge.capture(this, current);
            if (item == null) throw new IOException("当前剪贴板没有文本或图片");
            new ApiClient(current).push(item);
            return "已发送" + ("image".equals(item.kind) ? "图片" : "文本");
        }));
        receiveNow.setOnClickListener(view -> runAction("接收完成", current -> {
            synchronized (SyncCursor.TRANSACTION_LOCK) {
                ApiClient.FetchResult result = new ApiClient(current)
                        .fetch(SyncCursor.load(this, current));
                String message;
                if (result.item == null || current.deviceId.equals(result.item.origin)) {
                    message = "没有其他设备的新内容";
                } else {
                    message = applyReceived(current, result.item);
                }
                SyncCursor.save(this, current, result.revision);
                return message;
            }
        }));
        testConnection.setOnClickListener(view -> runAction("服务器可访问", current -> {
            if (!new ApiClient(current).health().optBoolean("ok", false)) {
                throw new IOException("服务器健康检查失败");
            }
            return "服务器可访问";
        }));
        stopSync.setOnClickListener(view -> {
            try {
                AppConfig.disableBackgroundFeatures(this);
                autoReceive.setChecked(false);
                serverEnabled.setChecked(false);
                config = AppConfig.load(this);
                stopSyncService();
                setStatus("warning", "正在停止后台服务");
            } catch (RuntimeException exception) {
                setStatus("error", "系统未能停止后台服务");
            }
        });
        backgroundSettings.setOnClickListener(view -> openBackgroundSettings());
        chooseFile.setOnClickListener(view -> openFilePicker());
        refreshFiles.setOnClickListener(
                view -> startFileAction(SyncService.ACTION_REFRESH_FILES, null, null));
        cancelTransfer.setOnClickListener(view -> {
            cancelTransfer.setEnabled(false);
            try {
                dispatchServiceIntent(new Intent(this, SyncService.class)
                        .setAction(SyncService.ACTION_CANCEL_TRANSFER));
            } catch (RuntimeException exception) {
                cancelTransfer.setEnabled(true);
                setStatus("error", "系统未能取消文件传输");
            }
        });
        findViewById(R.id.rootView).setOnDragListener(this::handleDragEvent);
    }

    private boolean handleDragEvent(View view, DragEvent event) {
        switch (event.getAction()) {
            case DragEvent.ACTION_DRAG_STARTED:
                boolean supported = event.getClipDescription() != null;
                setDropSessionVisible(supported);
                setDropActive(false);
                return supported;
            case DragEvent.ACTION_DRAG_ENTERED:
                setDropActive(true);
                return true;
            case DragEvent.ACTION_DRAG_LOCATION:
                return true;
            case DragEvent.ACTION_DRAG_EXITED:
                setDropActive(false);
                return true;
            case DragEvent.ACTION_DROP:
                setDropActive(false);
                return acceptDrop(event);
            case DragEvent.ACTION_DRAG_ENDED:
                setDropActive(false);
                setDropSessionVisible(false);
                return true;
            default:
                return false;
        }
    }

    private boolean acceptDrop(DragEvent event) {
        if (CLIPBOARD_ACTION_ACTIVE.get() || SyncService.isTransferActiveForUi()) {
            setStatus("warning", "上一项发送尚未完成");
            return false;
        }
        final IncomingContent incoming;
        try {
            incoming = parseClipData(event.getClipData());
        } catch (IllegalArgumentException | SecurityException exception) {
            setStatus("error", messageOf(exception, "拖入内容无法读取"));
            return false;
        }
        if (incoming.isEmpty()) {
            setStatus("error", "拖入内容中没有可发送的文件或文字");
            return false;
        }

        DragAndDropPermissions permissions = null;
        if (!incoming.uris.isEmpty()) {
            try {
                permissions = requestDragAndDropPermissions(event);
            } catch (RuntimeException exception) {
                setStatus("error", "系统未授予拖入文件的读取权限");
                return false;
            }
            if (permissions == null) {
                setStatus("error", "系统未授予拖入文件的读取权限");
                return false;
            }
            releaseDropPermissions();
            activeDropPermissions = permissions;
        }

        boolean accepted = sendIncomingContent(incoming, "拖入");
        if (!accepted) releaseDropPermissions();
        return accepted;
    }

    private void setDropActive(boolean active) {
        if (dropHint == null) return;
        dropHint.setActivated(active);
        dropHint.setText(active ? R.string.drop_release : R.string.drop_ready);
        dropHint.setTextColor(getColor(active ? R.color.primary : R.color.text_secondary));
    }

    private void setDropSessionVisible(boolean visible) {
        if (dropHint != null) dropHint.setVisibility(visible ? View.VISIBLE : View.GONE);
    }

    private void releaseDropPermissions() {
        DragAndDropPermissions permissions = activeDropPermissions;
        activeDropPermissions = null;
        if (permissions == null) return;
        try {
            permissions.release();
        } catch (RuntimeException ignored) {
            // Android also revokes drag grants when the receiving activity is destroyed.
        }
    }

    private void saveAndStart() {
        try {
            AppConfig candidate = readFields();
            candidate.validate();
            candidate.save(this);
            config = candidate;
            if (candidate.serviceRequired()) {
                requestNotificationsIfNeeded();
                try {
                    startSyncService();
                    setStatus("success", candidate.serverEnabled
                            ? "设置已保存，手机服务器正在启动"
                            : "设置已保存，后台自动接收正在启动");
                } catch (RuntimeException exception) {
                    stopServiceImmediately();
                    String detail = exception.getMessage();
                    setStatus("error", "设置已保存，但系统拒绝启动后台服务"
                            + (detail == null || detail.trim().isEmpty() ? "" : "：" + detail));
                }
            } else {
                stopSyncService();
                setStatus("success", "设置已保存，后台服务已关闭");
            }
            if (pendingShareIntent != null) {
                Intent share = pendingShareIntent;
                pendingShareIntent = null;
                sendSharedContent(share);
            }
        } catch (IllegalArgumentException exception) {
            setStatus("error", exception.getMessage());
        } catch (RuntimeException exception) {
            setStatus("error", "系统未能保存设置或启动后台服务");
        }
    }

    private AppConfig readFields() {
        float interval;
        int maximum;
        int port;
        int fileMaximum;
        try {
            interval = Float.parseFloat(pollInterval.getText().toString().trim());
            maximum = Integer.parseInt(maxSize.getText().toString().trim());
            port = Integer.parseInt(listenPort.getText().toString().trim());
            fileMaximum = Integer.parseInt(fileMaxSize.getText().toString().trim());
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException("同步间隔、端口和大小上限必须是数字");
        }
        String id = config == null || config.deviceId.isEmpty()
                ? UUID.randomUUID().toString() : config.deviceId;
        return new AppConfig(
                id,
                deviceName.getText().toString().trim(),
                serverUrl.getText().toString().trim(),
                token.getText().toString(),
                allowInsecureLan.isChecked(),
                requireHttpsForPublic.isChecked(),
                autoReceive.isChecked(),
                interval,
                maximum,
                serverEnabled.isChecked(),
                port,
                fileMaximum);
    }

    private void updatePublicHttpRisk(boolean httpsRequired) {
        publicHttpRisk.setVisibility(
                !serverEnabled.isChecked() && !httpsRequired ? View.VISIBLE : View.GONE);
    }

    private void updateServerMode(boolean enabled) {
        serverUrl.setEnabled(!enabled);
        allowInsecureLan.setEnabled(!enabled);
        requireHttpsForPublic.setEnabled(!enabled);
        localServerUrls.setVisibility(enabled ? View.VISIBLE : View.GONE);
        updatePublicHttpRisk(requireHttpsForPublic.isChecked());
        if (enabled) showServerUrls(null);
    }

    private void showServerUrls(String rendered) {
        if (!serverEnabled.isChecked()) {
            localServerUrls.setVisibility(View.GONE);
            return;
        }
        String value = rendered == null ? "" : rendered.trim();
        if (value.isEmpty()) {
            int port;
            try {
                port = Integer.parseInt(listenPort.getText().toString().trim());
            } catch (NumberFormatException ignored) {
                port = 8765;
            }
            value = String.join("\n", LocalAddressResolver.httpUrls(port));
        }
        localServerUrls.setText(value.isEmpty()
                ? getString(R.string.local_address_unavailable)
                : getString(R.string.local_server_urls, value));
        localServerUrls.setVisibility(View.VISIBLE);
    }

    private void openFilePicker() {
        Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try {
            startActivityForResult(picker, REQUEST_FILE);
        } catch (RuntimeException exception) {
            setStatus("error", "系统没有可用的文件选择器");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_FILE || resultCode != RESULT_OK || data == null) return;
        Uri uri = data.getData();
        if (uri == null) {
            setStatus("error", "所选文件无法读取");
            return;
        }
        try {
            getContentResolver().takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (SecurityException ignored) {
            // Some document providers grant access only for the current task.
        }
        startFileAction(SyncService.ACTION_UPLOAD_FILE, null, uri);
    }

    private void startFileAction(String action, String fileId, Uri uri) {
        List<Uri> uris = new ArrayList<>();
        if (uri != null) uris.add(uri);
        String serviceAction = SyncService.ACTION_UPLOAD_FILE.equals(action)
                ? SyncService.ACTION_UPLOAD_FILES : action;
        startServiceAction(serviceAction, fileId, uris, null);
    }

    private boolean startIncomingTransfer(IncomingContent incoming) {
        return startServiceAction(
                SyncService.ACTION_UPLOAD_FILES,
                null,
                incoming.uris,
                incoming.mergedText());
    }

    private boolean startServiceAction(
            String action, String fileId, List<Uri> uris, String sharedText) {
        boolean configSaved = false;
        boolean reconciliationDispatched = false;
        String stagedBatchToken = null;
        try {
            AppConfig candidate = readFields();
            candidate.validate();
            candidate.save(this);
            configSaved = true;
            config = candidate;
            requestNotificationsIfNeeded();
            if (candidate.serviceRequired()) {
                startSyncService();
                reconciliationDispatched = true;
            }

            List<Uri> serviceUris = uris;
            if (SyncService.ACTION_UPLOAD_FILES.equals(action)
                    && uris != null && !uris.isEmpty()) {
                IncomingFileRegistry.StagedBatch staged = IncomingFileRegistry.stage(this, uris);
                stagedBatchToken = staged.token;
                serviceUris = staged.uris;
            }

            Intent service = new Intent(this, SyncService.class).setAction(action);
            if (fileId != null) service.putExtra(SyncService.EXTRA_FILE_ID, fileId);
            if (stagedBatchToken != null) {
                service.putExtra(SyncService.EXTRA_INCOMING_BATCH_TOKEN, stagedBatchToken);
            }
            if (sharedText != null && !sharedText.isEmpty()) {
                service.putExtra(SyncService.EXTRA_SHARED_TEXT, sharedText);
            }
            if (serviceUris != null && !serviceUris.isEmpty()) {
                IncomingContentPolicy.requireItemCount(serviceUris.size());
                ClipData grants = ClipData.newRawUri("FlowClip file", serviceUris.get(0));
                for (int index = 1; index < serviceUris.size(); index++) {
                    grants.addItem(new ClipData.Item(serviceUris.get(index)));
                }
                service.setData(serviceUris.get(0));
                service.setClipData(grants);
                service.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            }
            setTransferBusy(true);
            dispatchServiceIntent(service);
            reconciliationDispatched = true;
            return true;
        } catch (IOException exception) {
            IncomingFileRegistry.discard(stagedBatchToken);
            if (configSaved && !reconciliationDispatched) stopServiceImmediately();
            setTransferBusy(false);
            setStatus("error", messageOf(exception, "无法准备拖入文件"));
            return false;
        } catch (IllegalArgumentException exception) {
            IncomingFileRegistry.discard(stagedBatchToken);
            if (configSaved && !reconciliationDispatched) stopServiceImmediately();
            setTransferBusy(false);
            setStatus("error", exception.getMessage());
            return false;
        } catch (RuntimeException exception) {
            IncomingFileRegistry.discard(stagedBatchToken);
            if (configSaved && !reconciliationDispatched) stopServiceImmediately();
            setTransferBusy(false);
            setStatus("error", "系统拒绝启动文件传输");
            return false;
        }
    }

    private void dispatchServiceIntent(Intent intent) {
        startForegroundService(intent);
    }

    private void showTransferProgress(String stage, long completed, long total) {
        setTransferBusy(true);
        String safeStage = stage == null || stage.trim().isEmpty() ? "正在传输" : stage;
        if (total <= 0L) {
            transferProgress.setIndeterminate(true);
            transferProgressText.setText(safeStage);
            return;
        }
        transferProgress.setIndeterminate(false);
        int percent = (int) Math.min(100L, Math.max(0L, completed) * 100L / total);
        transferProgress.setProgress(percent, true);
        transferProgressText.setText(getString(
                R.string.transfer_progress_format, safeStage, percent));
    }

    private void setTransferBusy(boolean busy) {
        boolean wasRunning = transferRunning;
        transferRunning = busy;
        transferStatus.setVisibility(busy ? View.VISIBLE : View.GONE);
        boolean idle = !busy && !actionRunning;
        chooseFile.setEnabled(idle);
        refreshFiles.setEnabled(idle);
        sendClipboard.setEnabled(idle);
        receiveNow.setEnabled(idle);
        testConnection.setEnabled(idle);
        saveStart.setEnabled(idle);
        cancelTransfer.setEnabled(busy);
        if (busy && !wasRunning) {
            transferProgress.setIndeterminate(true);
            transferProgressText.setText(R.string.transfer_starting);
        }
        if (!busy) {
            releaseDropPermissions();
            transferProgress.setIndeterminate(false);
            transferProgress.setProgress(0);
            transferProgressText.setText(R.string.transfer_starting);
        }
    }

    private void setActionBusy(boolean busy) {
        actionRunning = busy;
        setButtonsEnabled(!busy);
    }

    private void renderFilesJson(String encoded) {
        if (encoded == null || encoded.trim().isEmpty()) {
            renderFiles(new ArrayList<>());
            return;
        }
        try {
            JSONObject raw = new JSONObject(encoded);
            JSONArray values = raw.getJSONArray("files");
            if (values.length() > FileTransferPolicy.MAX_REMOTE_FILES) {
                throw new JSONException("文件列表过长");
            }
            List<FileMetadata> files = new ArrayList<>();
            for (int index = 0; index < values.length(); index++) {
                files.add(FileMetadata.fromJson(values.getJSONObject(index), Long.MAX_VALUE));
            }
            files.sort((left, right) -> Long.compare(right.createdAt, left.createdAt));
            renderFiles(files);
        } catch (JSONException | IllegalArgumentException exception) {
            renderFiles(new ArrayList<>());
            setStatus("error", "文件列表数据无效");
        }
    }

    private void renderFiles(List<FileMetadata> files) {
        fileList.removeAllViews();
        filesEmpty.setVisibility(files.isEmpty() ? View.VISIBLE : View.GONE);
        for (int index = 0; index < files.size(); index++) {
            if (index > 0) {
                View divider = new View(this);
                divider.setBackgroundColor(getColor(R.color.outline));
                LinearLayout.LayoutParams dividerParams = new LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, dp(1));
                dividerParams.setMargins(0, dp(8), 0, dp(8));
                fileList.addView(divider, dividerParams);
            }
            FileMetadata metadata = files.get(index);
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.VERTICAL);
            row.setPadding(0, dp(4), 0, dp(4));

            TextView name = new TextView(this);
            name.setText(metadata.filename);
            name.setTextColor(getColor(R.color.text_primary));
            name.setTextSize(15f);
            name.setMaxLines(2);
            name.setEllipsize(TextUtils.TruncateAt.END);
            row.addView(name, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT));

            TextView details = new TextView(this);
            String when = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                    .format(new Date(metadata.createdAt));
            details.setText(getString(
                    R.string.file_details_format, formatBytes(metadata.size), when));
            details.setTextColor(getColor(R.color.text_secondary));
            details.setTextSize(12f);
            LinearLayout.LayoutParams detailParams = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT);
            detailParams.topMargin = dp(3);
            row.addView(details, detailParams);

            LinearLayout actions = new LinearLayout(this);
            actions.setOrientation(LinearLayout.HORIZONTAL);
            Button download = fileActionButton(getString(R.string.download_file));
            download.setOnClickListener(view -> startFileAction(
                    SyncService.ACTION_DOWNLOAD_FILE, metadata.id, null));
            LinearLayout.LayoutParams downloadParams = new LinearLayout.LayoutParams(
                    0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
            downloadParams.setMarginEnd(dp(4));
            actions.addView(download, downloadParams);

            Button delete = fileActionButton(getString(R.string.delete_file));
            delete.setTextColor(getColor(R.color.danger));
            delete.setOnClickListener(view -> confirmDelete(metadata));
            LinearLayout.LayoutParams deleteParams = new LinearLayout.LayoutParams(
                    0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
            deleteParams.setMarginStart(dp(4));
            actions.addView(delete, deleteParams);

            LinearLayout.LayoutParams actionParams = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT);
            actionParams.topMargin = dp(6);
            row.addView(actions, actionParams);
            fileList.addView(row);
        }
    }

    private Button fileActionButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextSize(13f);
        button.setMinHeight(dp(40));
        button.setMinimumHeight(dp(40));
        return button;
    }

    private void confirmDelete(FileMetadata metadata) {
        new AlertDialog.Builder(this)
                .setTitle(R.string.delete_file)
                .setMessage(getString(R.string.delete_file_confirm, metadata.filename))
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.delete_file, (dialog, which) -> startFileAction(
                        SyncService.ACTION_DELETE_FILE, metadata.id, null))
                .show();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String formatBytes(long bytes) {
        if (bytes < 1024L) return bytes + " B";
        if (bytes < 1024L * 1024L) {
            return String.format(Locale.ROOT, "%.1f KB", bytes / 1024.0);
        }
        if (bytes < 1024L * 1024L * 1024L) {
            return String.format(Locale.ROOT, "%.1f MB", bytes / (1024.0 * 1024.0));
        }
        return String.format(Locale.ROOT, "%.2f GB", bytes / (1024.0 * 1024.0 * 1024.0));
    }

    private boolean runAction(String successDefault, ConfiguredAction action) {
        if (CLIPBOARD_ACTION_ACTIVE.get()) {
            Toast.makeText(this, "上一项操作尚未完成", Toast.LENGTH_SHORT).show();
            return false;
        }
        AppConfig current;
        try {
            current = readFields();
            current.validate();
        } catch (IllegalArgumentException exception) {
            setStatus("error", exception.getMessage());
            return false;
        }
        if (!CLIPBOARD_ACTION_ACTIVE.compareAndSet(false, true)) {
            Toast.makeText(this, "上一项操作尚未完成", Toast.LENGTH_SHORT).show();
            return false;
        }
        setActionBusy(true);
        notifyClipboardActionStateChanged();
        try {
            ACTIONS.execute(() -> {
                try {
                    String result = action.run(current);
                    publishActivityStatus("success",
                            result == null || result.isEmpty() ? successDefault : result);
                } catch (IOException | JSONException | RuntimeException exception) {
                    String message = exception.getMessage();
                    if (message == null || message.trim().isEmpty()) {
                        message = exception.getClass().getSimpleName();
                    }
                    publishActivityStatus("error", message);
                } finally {
                    CLIPBOARD_ACTION_ACTIVE.set(false);
                    notifyClipboardActionStateChanged();
                }
            });
        } catch (RejectedExecutionException exception) {
            CLIPBOARD_ACTION_ACTIVE.set(false);
            setActionBusy(false);
            notifyClipboardActionStateChanged();
            setStatus("error", "后台操作线程已停止");
            return false;
        }
        return true;
    }

    private void publishActivityStatus(String level, String message) {
        getSharedPreferences("flowclip_runtime", MODE_PRIVATE).edit()
                .putString("level", level)
                .putString("message", message)
                .apply();
        sendBroadcast(new Intent(SyncService.ACTION_STATUS)
                        .setPackage(getPackageName())
                        .putExtra(SyncService.EXTRA_LEVEL, level)
                        .putExtra(SyncService.EXTRA_MESSAGE, message),
                getPackageName() + ".permission.INTERNAL_STATUS");
    }

    private void notifyClipboardActionStateChanged() {
        sendBroadcast(new Intent(SyncService.ACTION_STATUS)
                        .setPackage(getPackageName())
                        .putExtra(EXTRA_CLIPBOARD_ACTION_ACTIVE,
                                CLIPBOARD_ACTION_ACTIVE.get()),
                getPackageName() + ".permission.INTERNAL_STATUS");
    }

    private String applyReceived(AppConfig current, ClipItem item) throws IOException {
        if ("text".equals(item.kind)) {
            ClipboardBridge.applyText(this, item);
            return "已收到文本并复制到剪贴板";
        }
        ImageStorage.SavedImage image = ImageStorage.save(this, item);
        return "图片已保存到 " + image.displayPath;
    }

    private void startSyncService() {
        Intent intent = new Intent(this, SyncService.class).setAction(SyncService.ACTION_START);
        startForegroundService(intent);
    }

    private void stopSyncService() {
        Intent intent = new Intent(this, SyncService.class).setAction(SyncService.ACTION_STOP);
        try {
            startService(intent);
        } catch (RuntimeException exception) {
            stopServiceImmediately();
            throw exception;
        }
    }

    private void stopServiceImmediately() {
        try {
            stopService(new Intent(this, SyncService.class));
        } catch (RuntimeException ignored) {
            // The original dispatch error remains the useful message for the user.
        }
    }

    private void requestNotificationsIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }

    private void openBackgroundSettings() {
        Intent details = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:" + getPackageName()));
        try {
            startActivity(details);
        } catch (RuntimeException exception) {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQUEST_NOTIFICATIONS && results.length > 0
                && results[0] != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, R.string.permission_notifications, Toast.LENGTH_LONG).show();
            setStatus("warning", getString(R.string.permission_notifications));
        }
    }

    private void setButtonsEnabled(boolean enabled) {
        boolean idle = enabled && !transferRunning;
        sendClipboard.setEnabled(idle);
        receiveNow.setEnabled(idle);
        testConnection.setEnabled(idle);
        chooseFile.setEnabled(idle);
        refreshFiles.setEnabled(idle);
        saveStart.setEnabled(idle);
    }

    private void setStatus(String level, String message) {
        statusText.setText(message);
        int color;
        if ("success".equals(level)) color = R.color.primary;
        else if ("error".equals(level)) color = R.color.danger;
        else if ("warning".equals(level)) color = R.color.warning;
        else color = R.color.text_secondary;
        statusText.setTextColor(getColor(color));
    }

    private void restoreRuntimeStatus() {
        android.content.SharedPreferences runtime =
                getSharedPreferences("flowclip_runtime", MODE_PRIVATE);
        String level = runtime.getString("level", "info");
        String message = runtime.getString("message", getString(R.string.not_configured));
        setStatus(level, message);
        showServerUrls(runtime.getString("serverUrls", ""));
        renderFilesJson(runtime.getString("filesJson", ""));
        setActionBusy(CLIPBOARD_ACTION_ACTIVE.get());
        setTransferBusy(SyncService.isTransferActiveForUi());
    }

    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    @Override
    protected void onStart() {
        super.onStart();
        IntentFilter filter = new IntentFilter(SyncService.ACTION_STATUS);
        String permission = getPackageName() + ".permission.INTERNAL_STATUS";
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(
                    statusReceiver,
                    filter,
                    permission,
                    null,
                    Context.RECEIVER_NOT_EXPORTED);
        } else {
            // The signature permission isolates this pre-Android 13 receiver from other apps.
            registerReceiver(statusReceiver, filter, permission, null);
        }
        receiverRegistered = true;
        restoreRuntimeStatus();
        AppConfig saved = AppConfig.load(this);
        if (saved.serviceRequired() && saved.isConfigured()) {
            try {
                startSyncService();
            } catch (RuntimeException exception) {
                setStatus("error", getString(R.string.sync_start_rejected));
            }
        }
    }

    @Override
    protected void onStop() {
        if (receiverRegistered) {
            unregisterReceiver(statusReceiver);
            receiverRegistered = false;
        }
        super.onStop();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        shareConsumed = false;
        consumeShareIntent(intent);
    }

    private void consumeShareIntent(Intent intent) {
        if (intent == null
                || (!Intent.ACTION_SEND.equals(intent.getAction())
                && !Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction()))) return;
        shareConsumed = true;
        Intent share = new Intent(intent);
        intent.setAction(null);
        setIntent(new Intent(this, MainActivity.class));
        if (!config.isConfigured()) {
            pendingShareIntent = share;
            if (token.getText().length() == 0) token.setText(AppConfig.generateToken());
            setStatus("warning", "请先填写与服务器相同的地址和密钥，再保存发送");
            return;
        }
        sendSharedContent(share);
    }

    private void sendSharedContent(Intent intent) {
        try {
            sendIncomingContent(parseSharedIntent(intent), "分享");
        } catch (IllegalArgumentException | SecurityException exception) {
            setStatus("error", messageOf(exception, "分享内容无法读取"));
        } catch (RuntimeException exception) {
            setStatus("error", "系统未能读取分享内容");
        }
    }

    private boolean sendIncomingContent(IncomingContent incoming, String source) {
        if (incoming == null || incoming.isEmpty()) {
            setStatus("error", source + "内容中没有可发送的文件或文字");
            return false;
        }
        if (CLIPBOARD_ACTION_ACTIVE.get() || SyncService.isTransferActiveForUi()) {
            setStatus("warning", "上一项发送尚未完成");
            return false;
        }
        String mergedText = incoming.mergedText();
        if (!incoming.uris.isEmpty()) return startIncomingTransfer(incoming);
        return runAction(source + "完成", current -> {
            ClipItem item = ClipItem.text(current.deviceId, mergedText);
            if (item.data.length > current.maxBytes()) throw new IOException("文本超过大小限制");
            new ApiClient(current).push(item);
            return "已通过" + source + "发送文字";
        });
    }

    private IncomingContent parseSharedIntent(Intent intent) {
        IncomingContent incoming = parseClipData(intent.getClipData());
        Uri data = intent.getData();
        if (data != null) appendUri(incoming, data);

        List<Uri> streams = sharedStreamUris(intent);
        IncomingContentPolicy.requireItemCount(streams.size());
        for (Uri stream : streams) appendUri(incoming, stream);

        CharSequence extraText = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        String text = extraText == null ? "" : extraText.toString();
        if (text.isEmpty()) {
            String html = intent.getStringExtra(Intent.EXTRA_HTML_TEXT);
            if (html != null && !html.isEmpty()) {
                text = Html.fromHtml(html, Html.FROM_HTML_MODE_LEGACY).toString();
            }
        }
        incoming.addText(text);
        return incoming;
    }

    private IncomingContent parseClipData(ClipData clipData) {
        IncomingContent incoming = new IncomingContent();
        if (clipData == null) return incoming;
        IncomingContentPolicy.requireItemCount(clipData.getItemCount());
        for (int index = 0; index < clipData.getItemCount(); index++) {
            ClipData.Item item = clipData.getItemAt(index);
            Uri uri = itemUri(item);
            String text = itemText(item);
            IncomingContentPolicy.ItemKind kind = IncomingContentPolicy.classify(
                    uri != null, uri == null ? null : uri.getScheme(), !text.isEmpty());
            if (kind == IncomingContentPolicy.ItemKind.URI) {
                requireExternalContentUri(uri);
                incoming.addUri(uri);
            } else if (kind == IncomingContentPolicy.ItemKind.TEXT) {
                incoming.addText(text);
            } else if (kind == IncomingContentPolicy.ItemKind.UNSUPPORTED_URI) {
                throw new IllegalArgumentException("仅支持系统提供的 content:// 文件");
            }
        }
        return incoming;
    }

    private static Uri itemUri(ClipData.Item item) {
        Uri direct = item.getUri();
        if (direct != null || item.getIntent() == null) return direct;
        Intent nested = item.getIntent();
        Uri data = nested.getData();
        if (data != null) return data;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            return nested.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class);
        }
        @SuppressWarnings("deprecation")
        Uri legacy = nested.getParcelableExtra(Intent.EXTRA_STREAM);
        return legacy;
    }

    private static String itemText(ClipData.Item item) {
        CharSequence value = item.getText();
        if (value != null && value.length() > 0) return value.toString();
        String html = item.getHtmlText();
        if (html == null || html.isEmpty()) return "";
        return Html.fromHtml(html, Html.FROM_HTML_MODE_LEGACY).toString();
    }

    private void appendUri(IncomingContent incoming, Uri uri) {
        if (uri == null) return;
        if (IncomingContentPolicy.classify(true, uri.getScheme(), false)
                != IncomingContentPolicy.ItemKind.URI) {
            throw new IllegalArgumentException("仅支持系统提供的 content:// 文件");
        }
        requireExternalContentUri(uri);
        incoming.addUri(uri);
    }

    private void requireExternalContentUri(Uri uri) {
        if (IncomingContentPolicy.isAppPrivateAuthority(
                getPackageName(), uri == null ? null : uri.getAuthority())) {
            throw new IllegalArgumentException("不接受 FlowClip 私有内容 URI");
        }
    }

    private static List<Uri> sharedStreamUris(Intent intent) {
        ArrayList<Uri> result = new ArrayList<>();
        if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Uri> values;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                values = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri.class);
            } else {
                @SuppressWarnings("deprecation")
                ArrayList<Uri> legacy = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
                values = legacy;
            }
            if (values != null) result.addAll(values);
        } else {
            Uri value;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                value = intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class);
            } else {
                @SuppressWarnings("deprecation")
                Uri legacy = intent.getParcelableExtra(Intent.EXTRA_STREAM);
                value = legacy;
            }
            if (value != null) result.add(value);
        }
        return result;
    }

    private static String messageOf(Throwable exception, String fallback) {
        String message = exception.getMessage();
        return message == null || message.trim().isEmpty() ? fallback : message;
    }

    @Override
    protected void onDestroy() {
        setDropActive(false);
        setDropSessionVisible(false);
        releaseDropPermissions();
        super.onDestroy();
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        state.putBoolean(STATE_SHARE_CONSUMED, shareConsumed);
        if (pendingShareIntent != null) {
            state.putParcelable(STATE_PENDING_SHARE, pendingShareIntent);
        }
    }

    private interface ConfiguredAction {
        String run(AppConfig config) throws IOException, JSONException;
    }

    private static final class IncomingContent {
        final List<Uri> uris = new ArrayList<>();
        final List<String> texts = new ArrayList<>();
        private final Set<String> uriKeys = new LinkedHashSet<>();
        private final Set<String> textKeys = new LinkedHashSet<>();

        void addUri(Uri uri) {
            String key = uri.normalizeScheme().toString();
            if (!uriKeys.add(key)) return;
            IncomingContentPolicy.requireItemCount(uris.size() + texts.size() + 1);
            uris.add(uri);
        }

        void addText(String text) {
            if (text == null || text.isEmpty() || !textKeys.add(text)) return;
            IncomingContentPolicy.requireItemCount(uris.size() + texts.size() + 1);
            texts.add(text);
        }

        String mergedText() {
            return IncomingContentPolicy.mergeText(texts);
        }

        boolean isEmpty() {
            return uris.isEmpty() && texts.isEmpty();
        }
    }
}
