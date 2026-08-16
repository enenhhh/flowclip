package com.flowclip.app;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

final class LocalAddressResolver {
    private LocalAddressResolver() {}

    static List<String> ipv4Addresses() {
        List<InetAddress> candidates = new ArrayList<>();
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            if (interfaces != null) {
                while (interfaces.hasMoreElements()) {
                    NetworkInterface network = interfaces.nextElement();
                    try {
                        if (!network.isUp() || network.isLoopback()) continue;
                    } catch (SocketException ignored) {
                        continue;
                    }
                    Enumeration<InetAddress> addresses = network.getInetAddresses();
                    while (addresses.hasMoreElements()) candidates.add(addresses.nextElement());
                }
            }
        } catch (SocketException ignored) {
            // An empty list is rendered as an unavailable address in the UI.
        }
        return filterIpv4(candidates);
    }

    static List<String> filterIpv4(Iterable<InetAddress> candidates) {
        Set<String> values = new LinkedHashSet<>();
        for (InetAddress address : candidates) {
            if (!(address instanceof Inet4Address)
                    || address.isAnyLocalAddress()
                    || address.isLoopbackAddress()
                    || address.isMulticastAddress()) {
                continue;
            }
            values.add(address.getHostAddress());
        }
        List<String> result = new ArrayList<>(values);
        Collections.sort(result);
        return result;
    }

    static List<String> httpUrls(int port) {
        List<String> result = new ArrayList<>();
        for (String address : ipv4Addresses()) {
            result.add("http://" + address + ":" + port);
        }
        return result;
    }
}
