package com.flowclip.app;

final class PayloadPolicy {
    static final int DEFAULT_MEGABYTES = 8;
    static final int MAX_MEGABYTES = 8;

    private PayloadPolicy() {}

    static void validateMegabytes(int value) {
        if (value < 1 || value > MAX_MEGABYTES) {
            throw new IllegalArgumentException("单条上限应为 1 到 8 MB");
        }
    }

    static long bytes(int megabytes) {
        validateMegabytes(megabytes);
        return megabytes * 1024L * 1024L;
    }

    static long maximumBase64Length(long maximumBytes) {
        if (maximumBytes < 0L || maximumBytes > Long.MAX_VALUE - 2L) {
            throw new IllegalArgumentException("内容大小限制无效");
        }
        return ((maximumBytes + 2L) / 3L) * 4L;
    }
}
