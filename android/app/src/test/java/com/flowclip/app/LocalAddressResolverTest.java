package com.flowclip.app;

import org.junit.Test;

import java.net.InetAddress;
import java.util.Arrays;
import java.util.List;

import static org.junit.Assert.assertEquals;

public final class LocalAddressResolverTest {
    @Test
    public void filterKeepsDistinctUsableIpv4AddressesInStableOrder() throws Exception {
        List<InetAddress> candidates = Arrays.asList(
                ipv4(192, 168, 1, 20),
                ipv4(127, 0, 0, 1),
                ipv4(10, 0, 0, 8),
                ipv4(224, 0, 0, 1),
                InetAddress.getByAddress(new byte[16]),
                ipv4(192, 168, 1, 20));

        assertEquals(Arrays.asList("10.0.0.8", "192.168.1.20"),
                LocalAddressResolver.filterIpv4(candidates));
    }

    private static InetAddress ipv4(int first, int second, int third, int fourth)
            throws Exception {
        return InetAddress.getByAddress(new byte[]{
                (byte) first, (byte) second, (byte) third, (byte) fourth});
    }
}
