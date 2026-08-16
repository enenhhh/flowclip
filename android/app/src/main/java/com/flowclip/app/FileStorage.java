package com.flowclip.app;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

final class FileStorage {
    private FileStorage() {}

    static PendingFile begin(Context context, FileMetadata metadata) throws IOException {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ContentValues values = new ContentValues();
            values.put(MediaStore.MediaColumns.DISPLAY_NAME, metadata.filename);
            values.put(MediaStore.MediaColumns.MIME_TYPE, metadata.mime);
            values.put(
                    MediaStore.MediaColumns.RELATIVE_PATH,
                    Environment.DIRECTORY_DOWNLOADS + "/FlowClip");
            values.put(MediaStore.MediaColumns.IS_PENDING, 1);
            ContentResolver resolver = context.getContentResolver();
            Uri uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (uri == null) throw new IOException("系统无法创建下载文件");
            OutputStream output = resolver.openOutputStream(uri, "w");
            if (output == null) {
                resolver.delete(uri, null, null);
                throw new IOException("系统无法写入下载文件");
            }
            return new PendingFile(
                    context,
                    uri,
                    null,
                    output,
                    "Download/FlowClip/" + metadata.filename,
                    true);
        }

        File directory = appSpecificDirectory(context);
        if (!directory.exists() && !directory.mkdirs()) {
            throw new IOException("无法创建文件目录");
        }
        File target = uniqueFile(directory, metadata.filename);
        return new PendingFile(
                context,
                TransferProvider.uriForFile(context, target),
                target,
                new FileOutputStream(target),
                "应用专属下载目录（卸载应用时删除）/FlowClip/" + target.getName(),
                false);
    }

    static InputStream open(Context context, Uri uri) throws IOException {
        InputStream input = context.getContentResolver().openInputStream(uri);
        if (input == null) throw new IOException("文件无法读取");
        return input;
    }

    static void delete(Context context, Uri uri) throws IOException {
        if (uri == null) return;
        if ((context.getPackageName() + ".files").equals(uri.getAuthority())) {
            File target = TransferProvider.fileForUri(context, uri);
            if (target.exists() && !target.delete()) throw new IOException("无法删除文件");
            return;
        }
        int deleted = context.getContentResolver().delete(uri, null, null);
        if (deleted < 1 && mediaStoreEntryExists(context, uri)) {
            throw new IOException("无法删除文件");
        }
    }

    private static boolean mediaStoreEntryExists(Context context, Uri uri) throws IOException {
        try (InputStream input = context.getContentResolver().openInputStream(uri)) {
            return input != null;
        } catch (FileNotFoundException exception) {
            return false;
        } catch (SecurityException exception) {
            throw new IOException("无法确认文件是否已删除", exception);
        }
    }

    static File appSpecificDirectory(Context context) {
        File root = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (root == null) root = new File(context.getFilesDir(), "Downloads");
        return new File(root, "FlowClip");
    }

    private static File uniqueFile(File directory, String filename) {
        File candidate = new File(directory, filename);
        if (!candidate.exists()) return candidate;
        int dot = filename.lastIndexOf('.');
        String base = dot > 0 ? filename.substring(0, dot) : filename;
        String extension = dot > 0 ? filename.substring(dot) : "";
        for (int index = 2; index < 10_000; index++) {
            candidate = new File(directory, base + "_" + index + extension);
            if (!candidate.exists()) return candidate;
        }
        return new File(directory, System.currentTimeMillis() + "_" + filename);
    }

    static final class PendingFile {
        final Uri uri;
        final String displayPath;
        final boolean publicDownloads;
        private final Context context;
        private final File file;
        private OutputStream output;
        private boolean complete;

        PendingFile(
                Context context,
                Uri uri,
                File file,
                OutputStream output,
                String displayPath,
                boolean publicDownloads) {
            this.context = context.getApplicationContext();
            this.uri = uri;
            this.file = file;
            this.output = output;
            this.displayPath = displayPath;
            this.publicDownloads = publicDownloads;
        }

        OutputStream output() {
            return output;
        }

        void closeOutput() throws IOException {
            if (output != null) {
                output.close();
                output = null;
            }
        }

        void finish() throws IOException {
            closeOutput();
            if (publicDownloads) {
                ContentValues ready = new ContentValues();
                ready.put(MediaStore.MediaColumns.IS_PENDING, 0);
                if (context.getContentResolver().update(uri, ready, null, null) != 1) {
                    discard();
                    throw new IOException("系统无法完成文件保存");
                }
            }
            complete = true;
        }

        void discard() {
            try {
                closeOutput();
            } catch (IOException ignored) {
                // Continue deleting the partial target.
            }
            try {
                if (file != null) {
                    if (file.exists()) file.delete();
                } else {
                    context.getContentResolver().delete(uri, null, null);
                }
            } catch (RuntimeException ignored) {
                // Cleanup is best effort after the primary transfer error.
            }
            complete = false;
        }

        boolean isComplete() {
            return complete;
        }
    }
}
