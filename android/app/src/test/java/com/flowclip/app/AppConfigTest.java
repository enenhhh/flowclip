package com.flowclip.app;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

public final class AppConfigTest {
    @Test
    public void serverModeUsesLoopbackAndKeepsForegroundServiceRequired() {
        AppConfig config = config(true, false, 8765);
        config.validate();
        assertEquals("http://127.0.0.1:8765", config.effectiveServerUrl());
        assertTrue(config.serviceRequired());
    }

    @Test
    public void manualRemoteModeDoesNotRequirePersistentService() {
        AppConfig config = config(false, false, 8765);
        config.validate();
        assertEquals("https://clip.example.com:443", config.effectiveServerUrl());
        assertFalse(config.serviceRequired());
    }

    @Test
    public void serverPortMustBeInUnprivilegedRange() {
        assertThrows(IllegalArgumentException.class,
                () -> config(true, false, 1023).validate());
        assertThrows(IllegalArgumentException.class,
                () -> config(true, false, 65536).validate());
    }

    private static AppConfig config(boolean serverEnabled, boolean autoReceive, int port) {
        return new AppConfig(
                "device-id",
                "Android",
                serverEnabled ? "" : "https://clip.example.com:443",
                "abcdefghijklmnop",
                false,
                true,
                autoReceive,
                2f,
                8,
                serverEnabled,
                port,
                256);
    }
}
