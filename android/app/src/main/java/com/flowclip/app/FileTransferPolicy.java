package com.flowclip.app;

final class FileTransferPolicy {
    static final int DEFAULT_MEGABYTES = 256;
    static final int MAX_MEGABYTES = 2048;
    static final int MAX_METADATA_BYTES = 16 * 1024;
    static final int MAX_LOCAL_FILES = 50;
    static final int MAX_REMOTE_FILES = 100;

    private FileTransferPolicy() {}

    static void validateMegabytes(int value) {
        if (value < 1 || value > MAX_MEGABYTES) {
            throw new IllegalArgumentException("文件上限应为 1 到 2048 MB");
        }
    }

    static long bytes(int megabytes) {
        validateMegabytes(megabytes);
        return megabytes * 1024L * 1024L;
    }
}
