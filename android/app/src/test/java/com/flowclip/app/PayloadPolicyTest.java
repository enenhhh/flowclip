package com.flowclip.app;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public final class PayloadPolicyTest {
    @Test
    public void acceptsOneThroughEightMegabytes() {
        PayloadPolicy.validateMegabytes(1);
        PayloadPolicy.validateMegabytes(8);
        assertEquals(8L * 1024L * 1024L, PayloadPolicy.bytes(8));
    }

    @Test
    public void rejectsValuesOutsideBound() {
        assertThrows(IllegalArgumentException.class,
                () -> PayloadPolicy.validateMegabytes(0));
        assertThrows(IllegalArgumentException.class,
                () -> PayloadPolicy.validateMegabytes(9));
    }

    @Test
    public void computesPreDecodeBase64Limit() {
        assertEquals(4L, PayloadPolicy.maximumBase64Length(1L));
        assertEquals(4L, PayloadPolicy.maximumBase64Length(3L));
        assertEquals(8L, PayloadPolicy.maximumBase64Length(4L));
    }
}
