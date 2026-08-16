package com.flowclip.app;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public final class BackoffPolicyTest {
    @Test
    public void firstFailureUsesConfiguredDelay() {
        assertEquals(2_000L, BackoffPolicy.delayMillis(2_000L, 1));
    }

    @Test
    public void subsequentFailuresDoubleUpToOneMinute() {
        assertEquals(4_000L, BackoffPolicy.delayMillis(2_000L, 2));
        assertEquals(8_000L, BackoffPolicy.delayMillis(2_000L, 3));
        assertEquals(60_000L, BackoffPolicy.delayMillis(2_000L, 16));
    }

    @Test
    public void delayNeverDropsBelowHalfSecond() {
        assertEquals(500L, BackoffPolicy.delayMillis(1L, 1));
    }
}
