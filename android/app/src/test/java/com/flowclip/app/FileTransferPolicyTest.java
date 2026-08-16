package com.flowclip.app;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public final class FileTransferPolicyTest {
    @Test
    public void byteLimitUsesLongArithmetic() {
        assertEquals(1024L * 1024L, FileTransferPolicy.bytes(1));
        assertEquals(2048L * 1024L * 1024L,
                FileTransferPolicy.bytes(FileTransferPolicy.MAX_MEGABYTES));
    }

    @Test
    public void megabyteLimitRejectsOutOfRangeValues() {
        assertThrows(IllegalArgumentException.class,
                () -> FileTransferPolicy.validateMegabytes(0));
        assertThrows(IllegalArgumentException.class,
                () -> FileTransferPolicy.validateMegabytes(2049));
    }
}
