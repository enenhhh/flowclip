package com.flowclip.app;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertThrows;

public final class EndpointPolicyTest {
    @Test
    public void httpsAllowsPublicHostButRequiresExplicitPort() {
        EndpointPolicy.validate("https://clip.example.com:443", false, true);
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("https://clip.example.com", false, true));
    }

    @Test
    public void httpRequiresExplicitConsentAndPrivateLiteral() {
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("http://192.168.1.20:8765", false, false));
        EndpointPolicy.validate("http://192.168.1.20:8765", true, true);
        EndpointPolicy.validate("http://10.0.0.2:8765", true, false);
        EndpointPolicy.validate("http://[fd00::2]:8765", true, true);
    }

    @Test
    public void tailscaleRangeIsAllowedWithConsent() {
        EndpointPolicy.validate("http://100.64.0.1:8765", true, true);
        EndpointPolicy.validate("http://100.127.255.254:8765", true, true);
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("http://100.128.0.1:8765", true, true));
    }

    @Test
    public void publicAndNamedHttpHostsAreRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("http://8.8.8.8:8765", true, true));
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("http://clip.local:8765", true, true));
    }

    @Test
    public void publicAndNamedHttpHostsCanBeAllowedWithoutLanConsent() {
        EndpointPolicy.validate("http://8.8.8.8:8765", false, false);
        EndpointPolicy.validate("http://clip.example.com:8080", false, false);
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("http://clip.example.com", false, false));
    }

    @Test
    public void endpointCannotContainRedirectableUrlParts() {
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("https://clip.example.com:443/api", false, true));
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("https://clip.example.com:443?next=http", false, true));
        assertThrows(IllegalArgumentException.class,
                () -> EndpointPolicy.validate("https://user@clip.example.com:443", false, true));
    }

    @Test
    public void cursorNamespaceNormalizesEndpointAndSeparatesTokens() {
        String first = EndpointPolicy.endpointKey("HTTPS://CLIP.EXAMPLE.COM:443", "token-a");
        String same = EndpointPolicy.endpointKey("https://clip.example.com:443", "token-a");
        String otherToken = EndpointPolicy.endpointKey("https://clip.example.com:443", "token-b");
        assertEquals(first, same);
        assertNotEquals(first, otherToken);
    }
}
