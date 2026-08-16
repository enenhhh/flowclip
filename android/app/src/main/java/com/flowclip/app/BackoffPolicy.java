package com.flowclip.app;

final class BackoffPolicy {
    static final int MAX_FAILURES = 16;
    private static final long MAX_DELAY_MILLIS = 60_000L;

    private BackoffPolicy() {}

    static long delayMillis(long baseDelayMillis, int failures) {
        long base = Math.max(500L, baseDelayMillis);
        int exponent = Math.max(0, Math.min(16, failures - 1));
        long multiplier = 1L << exponent;
        if (base > MAX_DELAY_MILLIS / multiplier) return MAX_DELAY_MILLIS;
        return Math.min(MAX_DELAY_MILLIS, base * multiplier);
    }
}
