package com.flowclip.app;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.function.BooleanSupplier;

final class ImageStorage {
    private static final int WRITE_CHUNK_BYTES = 64 * 1024;
    private static final Object STORAGE_LOCK = new Object();

    private ImageStorage() {}

    static SavedImage save(Context context, ClipItem item) throws IOException {
        PendingImage pending = prepare(context, item, () -> true);
        try {
            return pending.commit();
        } finally {
            pending.abort();
        }
    }

    static PendingImage prepare(Context context, ClipItem item, BooleanSupplier active)
            throws IOException {
        String filename = chooseFilename(item);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            return prepareMediaStore(context, item, filename, active);
        }
        File root = context.getExternalFilesDir(Environment.DIRECTORY_PICTURES);
        if (root == null) {
            root = new File(context.getFilesDir(), "Pictures");
        }
        File directory = new File(root, "FlowClip");
        if (!directory.exists() && !directory.mkdirs()) {
            throw new IOException("无法创建图片目录");
        }
        File pendingFile = File.createTempFile(".flowclip-", ".part", directory);
        boolean success = false;
        try {
            try (OutputStream output = new FileOutputStream(pendingFile)) {
                writeActive(output, item.data, active);
            }
            success = true;
            return PendingImage.forPrivateFile(context, directory, filename, pendingFile);
        } finally {
            if (!success) pendingFile.delete();
        }
    }

    private static PendingImage prepareMediaStore(
            Context context, ClipItem item, String filename, BooleanSupplier active)
            throws IOException {
        if (!active.getAsBoolean()) throw new IOException("图片接收已取消");
        ContentValues values = new ContentValues();
        values.put(MediaStore.Images.Media.DISPLAY_NAME, filename);
        values.put(MediaStore.Images.Media.MIME_TYPE, item.mime);
        values.put(MediaStore.Images.Media.RELATIVE_PATH,
                Environment.DIRECTORY_PICTURES + "/FlowClip");
        values.put(MediaStore.Images.Media.IS_PENDING, 1);
        ContentResolver resolver = context.getContentResolver();
        Uri uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
        if (uri == null) {
            throw new IOException("系统无法创建图片文件");
        }
        boolean success = false;
        try {
            try (OutputStream output = resolver.openOutputStream(uri, "w")) {
                if (output == null) {
                    throw new IOException("系统无法写入图片文件");
                }
                writeActive(output, item.data, active);
            }
            success = true;
            return PendingImage.forMediaStore(resolver, uri, filename);
        } finally {
            if (!success) {
                resolver.delete(uri, null, null);
            }
        }
    }

    private static void writeActive(
            OutputStream output, byte[] data, BooleanSupplier active) throws IOException {
        int offset = 0;
        while (offset < data.length) {
            if (!active.getAsBoolean()) throw new IOException("图片接收已取消");
            int length = Math.min(WRITE_CHUNK_BYTES, data.length - offset);
            output.write(data, offset, length);
            offset += length;
        }
        if (!active.getAsBoolean()) throw new IOException("图片接收已取消");
    }

    private static String chooseFilename(ClipItem item) {
        String filename = ClipboardBridge.sanitizeName(item.filename);
        if (filename.isEmpty() || !filename.contains(".")) {
            String extension = ClipboardBridge.extensionForMime(item.mime);
            filename = "FlowClip_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
                    .format(new Date()) + extension;
        }
        return filename;
    }

    private static File uniqueFile(File directory, String filename) {
        File candidate = new File(directory, filename);
        if (!candidate.exists()) {
            return candidate;
        }
        int dot = filename.lastIndexOf('.');
        String base = dot > 0 ? filename.substring(0, dot) : filename;
        String extension = dot > 0 ? filename.substring(dot) : "";
        for (int index = 2; index < 10_000; index++) {
            candidate = new File(directory, base + "_" + index + extension);
            if (!candidate.exists()) {
                return candidate;
            }
        }
        return new File(directory, System.currentTimeMillis() + "_" + filename);
    }

    static final class SavedImage {
        final Uri uri;
        final String displayPath;
        final boolean publicGallery;

        SavedImage(Uri uri, String displayPath, boolean publicGallery) {
            this.uri = uri;
            this.displayPath = displayPath;
            this.publicGallery = publicGallery;
        }
    }

    static final class PendingImage {
        private final Context context;
        private final ContentResolver resolver;
        private final Uri pendingUri;
        private final File directory;
        private final File pendingFile;
        private final String filename;
        private boolean committed;

        private PendingImage(
                Context context,
                ContentResolver resolver,
                Uri pendingUri,
                File directory,
                File pendingFile,
                String filename) {
            this.context = context;
            this.resolver = resolver;
            this.pendingUri = pendingUri;
            this.directory = directory;
            this.pendingFile = pendingFile;
            this.filename = filename;
        }

        static PendingImage forMediaStore(
                ContentResolver resolver, Uri pendingUri, String filename) {
            return new PendingImage(null, resolver, pendingUri, null, null, filename);
        }

        static PendingImage forPrivateFile(
                Context context, File directory, String filename, File pendingFile) {
            return new PendingImage(context, null, null, directory, pendingFile, filename);
        }

        SavedImage commit() throws IOException {
            if (committed) throw new IOException("图片已经完成保存");
            SavedImage saved;
            if (pendingUri != null) {
                ContentValues ready = new ContentValues();
                ready.put(MediaStore.Images.Media.IS_PENDING, 0);
                if (resolver.update(pendingUri, ready, null, null) != 1) {
                    throw new IOException("系统无法完成图片保存");
                }
                saved = new SavedImage(
                        pendingUri, "Pictures/FlowClip/" + filename, true);
            } else {
                File target;
                Uri targetUri;
                synchronized (STORAGE_LOCK) {
                    target = uniqueFile(directory, filename);
                    targetUri = ImageProvider.uriForFile(context, target);
                    if (!pendingFile.renameTo(target)) {
                        throw new IOException("系统无法完成图片保存");
                    }
                }
                saved = new SavedImage(
                        targetUri,
                        "应用专属图片目录（卸载应用时删除）/FlowClip/"
                                + target.getName(),
                        false);
            }
            committed = true;
            return saved;
        }

        void abort() {
            if (committed) return;
            if (pendingUri != null) {
                resolver.delete(pendingUri, null, null);
            } else if (pendingFile != null) {
                pendingFile.delete();
            }
        }
    }
}
